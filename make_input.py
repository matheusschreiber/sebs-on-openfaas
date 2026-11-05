#!/usr/bin/env python3
"""Create input.json for a SeBS benchmark and upload its input files to the host's S3-compatible storage.

Mirrors Benchmark.prepare_input() in sebs/benchmark.py (SeBS 1.2.x): one benchmarks bucket, with
prefixes '<benchmark>-<i>-input' / '<benchmark>-<i>-output'.
"""
import argparse, importlib.util, json, os

import minio

ap = argparse.ArgumentParser()
ap.add_argument("benchmark_dir", help=".../benchmarks/200.multimedia/210.thumbnailer")
ap.add_argument("--data-dir", default=None, help="the benchmark's folder inside benchmarks-data/ (omit if it has none)")
ap.add_argument("--size", default="test", choices=["test", "small", "large"])
ap.add_argument("--endpoint", help="host:port of the storage as seen from THIS machine, e.g. 127.0.0.1:9011")
ap.add_argument("--access-key"); ap.add_argument("--secret-key")
ap.add_argument("--nosql-endpoint", help="host:port of the DynamoDB-compatible NoSQL endpoint")
ap.add_argument("--bucket", default="sebs-benchmarks")
ap.add_argument("--out", default="input.json")
a = ap.parse_args()

name = os.path.basename(os.path.normpath(a.benchmark_dir))
spec = importlib.util.spec_from_file_location("bench_input", os.path.join(a.benchmark_dir, "input.py"))
mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)

bucket_counts = mod.buckets_count() if hasattr(mod, "buckets_count") else (0, 0)
uses_object = any(bucket_counts)
uses_nosql = hasattr(mod, "allocate_nosql")
if uses_object and uses_nosql:
    raise SystemExit("%s requires both object and NoSQL storage; this script supports one storage type at a time." % name)
if uses_object and not (a.endpoint and a.access_key and a.secret_key):
    raise SystemExit("%s requires --endpoint, --access-key, and --secret-key." % name)
if uses_nosql and not a.nosql_endpoint:
    raise SystemExit("%s requires --nosql-endpoint." % name)

in_prefixes, out_prefixes, upload, bucket = [], [], None, None
if uses_object:
    n_in, n_out = bucket_counts
    in_prefixes = ["%s-%d-input" % (name, i) for i in range(n_in)]
    out_prefixes = ["%s-%d-output" % (name, i) for i in range(n_out)]
    client = minio.Minio(a.endpoint, access_key=a.access_key, secret_key=a.secret_key, secure=False)
    bucket = a.bucket
    if not client.bucket_exists(bucket):
        client.make_bucket(bucket)

    def upload(path_idx, file, filepath):          # same behaviour as S3Compatible.uploader_func
        client.fput_object(bucket, os.path.join(in_prefixes[path_idx], file), filepath)

nosql_upload = None
if uses_nosql:
    import boto3

    nosql_client = boto3.resource(
        "dynamodb",
        region_name="None",
        aws_access_key_id="None",
        aws_secret_access_key="None",
        endpoint_url="http://" + a.nosql_endpoint,
    )
    nosql_tables = {}
    for table_name, properties in mod.allocate_nosql().items():
        key_schema = [{"AttributeName": properties["primary_key"], "KeyType": "HASH"}]
        attribute_definitions = [{"AttributeName": properties["primary_key"], "AttributeType": "S"}]
        if properties.get("secondary_key"):
            key_schema.append({"AttributeName": properties["secondary_key"], "KeyType": "RANGE"})
            attribute_definitions.append({"AttributeName": properties["secondary_key"], "AttributeType": "S"})
        try:
            nosql_client.meta.client.describe_table(TableName=table_name)
        except nosql_client.meta.client.exceptions.ResourceNotFoundException:
            nosql_client.create_table(
                TableName=table_name,
                BillingMode="PAY_PER_REQUEST",
                KeySchema=key_schema,
                AttributeDefinitions=attribute_definitions,
            )
        table = nosql_client.Table(table_name)
        table.wait_until_exists()
        nosql_tables[table_name] = table

    def nosql_upload(benchmark, table_name, data, primary_key, secondary_key):
        item = dict(data)
        for key in (primary_key, secondary_key):
            if key is not None:
                item[key[0]] = key[1]
        nosql_tables[table_name].put_item(Item=item)

cfg = mod.generate_input(a.data_dir, a.size, bucket, in_prefixes, out_prefixes, upload, nosql_upload)
json.dump(cfg, open(a.out, "w"), indent=2)
print("wrote", a.out, json.dumps(cfg)[:200])