s3 = boto3.client(
    's3',
    endpoint_url=BEGET_S3_ENDPOINT,
    aws_access_key_id=BEGET_S3_ACCESS_KEY,
    aws_secret_access_key=BEGET_S3_SECRET_KEY,
    region_name='ru-1',
    config=boto3.session.Config(
        signature_version='s3v4',
        s3={'addressing_style': 'path'}
    )
)

