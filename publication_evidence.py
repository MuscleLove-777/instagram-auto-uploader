"""Local evidence records, called only after a provider confirms publication."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit
import re

POLICY_VERSION = 'linktree-only-v1'


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def content_hashes(content):
    return {key: hashlib.sha256(value.encode('utf-8')).hexdigest()
            for key, value in content.items() if key in {'title', 'caption'} and isinstance(value, str)}


def tumblr_post_locator(blog_name, post_id):
    host = urlsplit('https://' + str(blog_name).split('://')[-1]).hostname
    if not host:
        raise ValueError('public blog hostname unavailable')
    if '.' not in host:
        host += '.tumblr.com'
    return f'https://{host}/post/{post_id}'


def build_publication_receipt(platform, post_id, source_locator, content,
                              published_at=None, variants=None):
    """Fingerprint exactly the submitted public fields after API success.

    This confirms the create response/identity, not a subsequent caption
    readback, click, reaction, purchase or completed experiment.
    """
    identity = str(post_id or '').strip()
    locator = str(source_locator or '').strip() or f'provider-record:{platform}:{quote(identity, safe="")}'
    parsed = urlsplit(locator)
    valid_locator = (parsed.scheme in {'http', 'https', 'at'} and bool(parsed.netloc)) or parsed.scheme == 'provider-record'
    if not identity or identity.lower() in {'none', 'null', 'true', 'false'} or not valid_locator:
        raise ValueError('publication receipt requires a provider-confirmed identity and valid locator')
    stamp = published_at or utc_now()
    if datetime.fromisoformat(stamp.replace('Z', '+00:00')).tzinfo is None:
        raise ValueError('publication timestamp must contain a timezone')
    public = {key: value for key, value in content.items()
              if key in {'title', 'caption'} and isinstance(value, str)}
    if not public:
        raise ValueError('publication receipt requires the final submitted content')
    result = {
        'version': 1, 'platform': str(platform), 'post_id': identity,
        'source_locator': locator, 'published_at': stamp,
        'publication_time_source': 'provider_timestamp' if published_at else 'local_publish_ack',
        'verified': True, 'verification_scope': 'provider_create_response',
        'operation': 'create', 'provider_stage': 'publish',
        'receipt_status': 'api_confirmed', 'body_readback_verified': False,
        'source_locator_kind': 'provider_record' if parsed.scheme in {'provider-record', 'at'} else 'public_url',
        'canonical_policy_version': POLICY_VERSION,
        'content': public, 'content_hashes': content_hashes(public),
    }
    if variants:
        result['variants'] = {str(key): str(value) for key, value in variants.items() if value is not None}
    return result


def identity_from_url(platform, value):
    """Extract identities only from known published-work URL shapes."""
    try:
        parsed = urlsplit(str(value or ''))
    except ValueError:
        return None
    if parsed.scheme not in {'https', 'http'}:
        return None
    host = (parsed.hostname or '').lower()
    patterns = {
        'ameblo': ('ameblo.jp', r'/[^/]+/entry-(\d+)\.html'),
        'pixiv': ('pixiv.net', r'/artworks/(\d+)'),
        'pinterest': ('pinterest.com', r'/pin/(\d+)'),
        'tiktok': ('tiktok.com', r'/@[^/]+/video/(\d+)'),
        'instagram': ('instagram.com', r'/(?:p|reel|tv)/([\w-]+)'),
        'galleria': ('galleria.emotionflow.com', r'/\d+/(\d+)\.html'),
    }
    if platform == 'mixi' and host in {'mixi.jp', 'www.mixi.jp'} and parsed.path.endswith('/view_diary.pl'):
        identity = parse_qs(parsed.query).get('id', [''])[0]
        return identity if identity.isdigit() else None
    if platform == 'gelbooru' and host in {'gelbooru.com', 'www.gelbooru.com'}:
        query = parse_qs(parsed.query)
        identity = query.get('id', [''])[0]
        return identity if query.get('page') == ['post'] and query.get('s') == ['view'] and identity.isdigit() else None
    if platform not in patterns:
        return None
    domain, pattern = patterns[platform]
    if host != domain and not host.endswith('.' + domain):
        return None
    match = re.fullmatch(pattern + '/?', parsed.path)
    return match.group(1) if match else None


def record_publication_result(platform, content, post_id=None, source_locator=None,
                              *, confirmed=False, variants=None, output_path=None,
                              provider_stage='publish', receipt_status='api_confirmed',
                              published_at=None, publication_time_known=True):
    """Keep existing provider contracts; record unbound success as HOLD.

    A filename-only ledger can keep its current schema and use the adjacent
    publication_receipts.jsonl. Failed/dry-run branches never call this with
    confirmed=True. An upload-container ID is not a published-post ID.
    """
    if not confirmed:
        return None
    content = {key: value for key, value in content.items()
               if key in {'title', 'caption'} and isinstance(value, str)}
    identity = post_id or identity_from_url(platform, source_locator)
    if provider_stage == 'publish' and identity:
        try:
            record = build_publication_receipt(platform, identity, source_locator, content,
                                               published_at=published_at, variants=variants)
        except (ValueError, TypeError):
            identity = None
    if provider_stage != 'publish' or not identity:
        record = {
            'version': 1, 'platform': platform, 'verified': False,
            'receipt_status': 'HOLD_IDENTITY_UNAVAILABLE' if provider_stage == 'publish' else 'HOLD_NOT_PUBLISHED',
            'observed_at': utc_now(), 'provider_stage': provider_stage,
            'operation': 'create_attempt', 'body_readback_verified': False,
            'verification_scope': 'unverified_publish_acceptance' if provider_stage == 'publish' else 'upload_acceptance',
            'content': content, 'content_hashes': content_hashes(content),
            'canonical_policy_version': POLICY_VERSION,
        }
    else:
        record['receipt_status'] = receipt_status
        record['body_readback_verified'] = receipt_status == 'readback_confirmed'
        record['verification_scope'] = ('ui_create_confirmation' if receipt_status in {'readback_confirmed', 'ui_confirmed'}
                                         else 'provider_create_response')
        if receipt_status == 'ui_confirmed' and published_at is None:
            record['publication_time_source'] = 'local_ui_confirmation'
        if not publication_time_known:
            record['confirmed_at'] = record.pop('published_at')
            record['publication_time_status'] = 'unavailable'
            record['publication_time_source'] = 'unavailable'
    directory = Path(os.environ.get('PUBLICATION_EVIDENCE_DIR') or Path(__file__).parent)
    path = Path(output_path) if output_path is not None else directory / 'publication_receipts.jsonl'
    try:
        with path.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + '\n')
        record['persistence_status'] = 'saved'
    except OSError:
        # Do not turn a confirmed external publication into a retryable upload
        # failure merely because secondary local evidence storage failed.
        record['persistence_status'] = 'HOLD_WRITE_FAILED'
        print('HOLD_PUBLICATION_EVIDENCE_WRITE_FAILED')
    return record


def native_observation(metric, value, content=None):
    """Never substitute zero for missing/unread native counts."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError('native counter unavailable or invalid')
    result = {metric: value, 'observed_at': utc_now(), 'native_verified': True,
              'observation_complete': True}
    if content:
        result['content_hashes'] = content_hashes(content)
    return result


def record_collection_run(output_path, platform, fetched, failures):
    """Append an attempt record without refreshing untouched historical rows."""
    path = Path(output_path).with_name('engagement_collection_runs.jsonl')
    status = 'PARTIAL' if fetched and failures else 'ERROR' if failures else 'OK' if fetched else 'EMPTY'
    record = {'version': 1, 'platform': platform, 'observed_at': utc_now(),
              'status': status, 'fetched_count': len(fetched),
              'failures': [str(item) for item in failures]}
    with path.open('a', encoding='utf-8') as stream:
        stream.write(json.dumps(record, ensure_ascii=False) + '\n')
    return record
