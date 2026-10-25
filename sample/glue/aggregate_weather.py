"""GHCN-Daily のその年の観測値を、国 × 月 × 要素で集計して Parquet に書く。"""

import sys

from awsglue.context import GlueContext
from awsglue.job import Job
from awsglue.utils import getResolvedOptions
from pyspark.context import SparkContext
from pyspark.sql import functions as F

ELEMENTS = ["TMAX", "TMIN", "PRCP", "SNWD"]

args = getResolvedOptions(sys.argv, ["JOB_NAME", "SOURCE_PATH", "OUTPUT_PATH"])
glue_context = GlueContext(SparkContext.getOrCreate())
spark = glue_context.spark_session
job = Job(glue_context)
job.init(args["JOB_NAME"], args)

observations = (
    spark.read.option("header", "true")
    .csv(args["SOURCE_PATH"])
    .where(F.col("Q_FLAG").isNull() & F.col("ELEMENT").isin(ELEMENTS))
    .select(
        F.substring("ID", 1, 2).alias("country"),
        F.substring("DATE", 1, 6).alias("month"),
        "ELEMENT",
        F.col("DATA_VALUE").cast("int").alias("value"),
    )
)

summary = observations.groupBy("country", "month", "ELEMENT").agg(
    F.count("*").alias("observations"),
    F.avg("value").alias("avg_value"),
    F.max("value").alias("max_value"),
)

summary.write.mode("overwrite").partitionBy("ELEMENT").parquet(args["OUTPUT_PATH"])
job.commit()
