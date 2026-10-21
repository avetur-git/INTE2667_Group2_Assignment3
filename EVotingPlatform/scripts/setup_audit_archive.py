"""REQ-05: prepare the write-once audit archive in MinIO.

Run once with the MinIO ROOT credentials (never given to the app):

    python scripts/setup_audit_archive.py

Creates:
  - a bucket with object locking on and a default COMPLIANCE retention
  - audit-writer: may only upload objects and set their retention. It cannot
    read, list, delete or change retention downwards (compliance mode).
  - audit-reader: may only read, for `flask verify-audit-archive`.

Settings come from the environment (see .env.example):
  AUDIT_ARCHIVE_ENDPOINT, AUDIT_ARCHIVE_BUCKET, MINIO_ROOT_USER, MINIO_ROOT_PASSWORD,
  AUDIT_ARCHIVE_ACCESS_KEY / _SECRET_KEY (writer),
  AUDIT_ARCHIVE_READER_ACCESS_KEY / _SECRET_KEY (reader), AUDIT_ARCHIVE_DEFAULT_DAYS
"""
import os
import sys

from minio import Minio
from minio.commonconfig import COMPLIANCE
from minio.credentials import StaticProvider
from minio.minioadmin import MinioAdmin
from minio.objectlockconfig import DAYS, ObjectLockConfig


def _policy(statements):
    return {'Version': '2012-10-17', 'Statement': statements}


def writer_policy(bucket):
    return _policy([{'Effect': 'Allow', 'Action': ['s3:PutObject', 's3:PutObjectRetention'],
                     'Resource': [f'arn:aws:s3:::{bucket}/*']}])


def reader_policy(bucket):
    return _policy([
        {'Effect': 'Allow', 'Action': ['s3:ListBucket', 's3:ListBucketVersions'],
         'Resource': [f'arn:aws:s3:::{bucket}']},
        {'Effect': 'Allow',
         'Action': ['s3:GetObject', 's3:GetObjectVersion', 's3:GetObjectRetention'],
         'Resource': [f'arn:aws:s3:::{bucket}/*']},
    ])


def setup(endpoint, root_user, root_password, bucket, writer, reader, default_days=1, secure=False):
    """Idempotent: safe to run again. `writer` and `reader` are (access_key, secret_key)."""
    root = Minio(endpoint, access_key=root_user, secret_key=root_password, secure=secure,
                 region='us-east-1')
    if not root.bucket_exists(bucket):
        root.make_bucket(bucket, object_lock=True)   # object lock can only be enabled at creation
    root.set_object_lock_config(bucket, ObjectLockConfig(COMPLIANCE, default_days, DAYS))

    admin = MinioAdmin(endpoint=endpoint, credentials=StaticProvider(root_user, root_password),
                       secure=secure)
    for role, (access_key, secret_key), policy in (('writer', writer, writer_policy(bucket)),
                                                    ('reader', reader, reader_policy(bucket))):
        name = f'{bucket}-{role}'
        admin.policy_add(name, policy=policy)
        admin.user_add(access_key, secret_key)
        try:
            admin.attach_policy([name], user=access_key)
        except Exception:
            pass   # already attached on a previous run
    return bucket


def main():
    env = os.environ
    missing = [k for k in ('AUDIT_ARCHIVE_ENDPOINT', 'MINIO_ROOT_USER', 'MINIO_ROOT_PASSWORD',
                           'AUDIT_ARCHIVE_ACCESS_KEY', 'AUDIT_ARCHIVE_SECRET_KEY',
                           'AUDIT_ARCHIVE_READER_ACCESS_KEY', 'AUDIT_ARCHIVE_READER_SECRET_KEY')
               if not env.get(k)]
    if missing:
        print('Missing settings: ' + ', '.join(missing))
        return 1
    bucket = setup(env['AUDIT_ARCHIVE_ENDPOINT'], env['MINIO_ROOT_USER'], env['MINIO_ROOT_PASSWORD'],
                   env.get('AUDIT_ARCHIVE_BUCKET', 'audit-archive'),
                   (env['AUDIT_ARCHIVE_ACCESS_KEY'], env['AUDIT_ARCHIVE_SECRET_KEY']),
                   (env['AUDIT_ARCHIVE_READER_ACCESS_KEY'], env['AUDIT_ARCHIVE_READER_SECRET_KEY']),
                   int(env.get('AUDIT_ARCHIVE_DEFAULT_DAYS', '1')),
                   env.get('AUDIT_ARCHIVE_SECURE', 'false').lower() == 'true')
    print(f'Audit archive ready: bucket {bucket!r} with COMPLIANCE object lock, '
          'writer (upload only) and reader (read only) users.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
