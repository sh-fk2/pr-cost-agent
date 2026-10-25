"""GHCN-Daily のその年の観測値を、国 × 月 × 要素で集計して Parquet に書く。"""

import os
import shutil
from pathlib import Path
from typing import Any

import boto3
import duckdb

SOURCE_BUCKET = os.environ["SOURCE_BUCKET"]
SOURCE_KEY = os.environ["SOURCE_KEY"]
OUTPUT_BUCKET = os.environ["OUTPUT_BUCKET"]
OUTPUT_PREFIX = "summary"
ELEMENTS = frozenset({"TMAX", "TMIN", "PRCP", "SNWD"})
WORK_DIR = Path("/tmp")
SPILL_DIR = WORK_DIR / "duckdb"
COLUMNS = (
    "{'ID': 'VARCHAR', 'DATE': 'VARCHAR', 'ELEMENT': 'VARCHAR', 'DATA_VALUE': 'INTEGER',"
    " 'M_FLAG': 'VARCHAR', 'Q_FLAG': 'VARCHAR', 'S_FLAG': 'VARCHAR', 'OBS_TIME': 'VARCHAR'}"
)

s3 = boto3.client("s3")


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    elements = sorted(event["elements"])
    unknown = set(elements) - ELEMENTS
    if not elements or unknown:
        raise ValueError(f"対象外の要素があります: {sorted(unknown)}")
    source = WORK_DIR / "source.csv"
    output = WORK_DIR / "summary"
    try:
        s3.download_file(SOURCE_BUCKET, SOURCE_KEY, str(source))
        rows = _aggregate(source, output, elements)
        keys = [_upload(output, element) for element in elements]
    finally:
        # 温まったコンテナで /tmp が埋まったままにならないよう、途中で失敗しても消す
        source.unlink(missing_ok=True)
        shutil.rmtree(output, ignore_errors=True)
        shutil.rmtree(SPILL_DIR, ignore_errors=True)
    return {"rows": rows, "keys": keys}


def _aggregate(source: Path, output: Path, elements: list[str]) -> dict[str, int]:
    with duckdb.connect(config={"temp_directory": str(SPILL_DIR)}) as connection:
        # elements は ELEMENTS で検証済み。COPY はパラメータを受け付けないため埋め込む
        element_list = ", ".join(f"'{element}'" for element in elements)
        connection.execute(
            f"""
            COPY (
                SELECT substr(ID, 1, 2) AS country, substr(DATE, 1, 6) AS month, ELEMENT,
                       count(*) AS observations, avg(DATA_VALUE) AS avg_value,
                       max(DATA_VALUE) AS max_value
                FROM read_csv('{source}', header = true, columns = {COLUMNS})
                WHERE Q_FLAG IS NULL AND ELEMENT IN ({element_list})
                GROUP BY ALL
            ) TO '{output}' (FORMAT parquet, PARTITION_BY (ELEMENT), OVERWRITE)
            """
        )
        counts = connection.execute(
            f"SELECT ELEMENT, count(*) FROM read_parquet('{output}/*/*.parquet',"
            " hive_partitioning = true) GROUP BY ELEMENT"
        ).fetchall()
    return {str(element): int(count) for element, count in counts}


def _upload(output: Path, element: str) -> str:
    files = sorted((output / f"ELEMENT={element}").glob("*.parquet"))
    if len(files) != 1:
        raise RuntimeError(f"{element} の出力が1ファイルではありません: {len(files)}")
    key = f"{OUTPUT_PREFIX}/ELEMENT={element}/part-0.parquet"
    s3.upload_file(str(files[0]), OUTPUT_BUCKET, key)
    return key
