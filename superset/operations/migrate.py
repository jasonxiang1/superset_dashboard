# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.
"""Explicit, transactional upgrade of an external Operations schema.

Run with OPERATIONS_MIGRATION_DATABASE_URI and --schema, after taking a backup.
This module is never invoked during application startup.
"""

import argparse
import os
import re

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Connection

from superset.operations.tables import OperationsTables


def upgrade(connection: Connection, schema: str) -> None:
    """Preserve legacy data while allowing hours-only records and linked summaries."""
    if connection.dialect.name != "postgresql" or not re.fullmatch(
        r"[a-z_][a-z0-9_]{0,62}", schema
    ):
        raise ValueError("A PostgreSQL connection and valid schema are required.")
    inspector = inspect(connection)
    required = {
        "operations_labor_entries",
        "operations_summaries",
        "operations_attachments",
    }
    if not required.issubset(inspector.get_table_names(schema=schema)):
        raise ValueError("Provision the existing Operations schema before upgrading.")
    for column in ("hourly_rate", "cost", "currency"):
        connection.execute(
            text(
                f'ALTER TABLE "{schema}".operations_labor_entries '
                f"ALTER COLUMN {column} DROP NOT NULL"
            )
        )
    connection.execute(
        text(
            f'ALTER TABLE "{schema}".operations_attachments ALTER '
            f"COLUMN project_id DROP NOT NULL"
        )
    )
    connection.execute(
        text(
            f'ALTER TABLE "{schema}".operations_attachments ADD COLUMN '
            f"IF NOT EXISTS summary_id UUID"
        )
    )
    constraints = {
        item["name"]
        for item in inspector.get_unique_constraints(
            "operations_summaries", schema=schema
        )
    }
    if "uq_operations_summaries_workspace_id" not in constraints:
        connection.execute(
            text(
                f'ALTER TABLE "{schema}".operations_summaries ADD '
                f"CONSTRAINT uq_operations_summaries_workspace_id "
                f"UNIQUE (workspace_id, id)"
            )
        )
    foreign_keys = {
        item["name"]
        for item in inspector.get_foreign_keys("operations_attachments", schema=schema)
    }
    if "fk_operations_attachments_summary" not in foreign_keys:
        connection.execute(
            text(
                f'ALTER TABLE "{schema}".operations_attachments ADD '
                f"CONSTRAINT fk_operations_attachments_summary FOREIGN "
                f"KEY (workspace_id, summary_id) REFERENCES "
                f'"{schema}".operations_summaries (workspace_id, id)'
            )
        )
    checks = {
        item["name"]
        for item in inspector.get_check_constraints(
            "operations_attachments", schema=schema
        )
    }
    if "ck_operations_attachment_parent" not in checks:
        connection.execute(
            text(
                f'ALTER TABLE "{schema}".operations_attachments ADD '
                f"CONSTRAINT ck_operations_attachment_parent CHECK "
                f"((project_id IS NULL) <> (summary_id IS NULL))"
            )
        )
    connection.execute(
        text(
            f"CREATE INDEX IF NOT EXISTS "
            f"ix_operations_attachments_summary ON "
            f'"{schema}".operations_attachments (workspace_id, '
            f"summary_id)"
        )
    )
    OperationsTables(schema).proposals.create(connection, checkfirst=True)


def main() -> None:
    """Upgrade only the explicitly supplied database and schema."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--schema", required=True)
    args = parser.parse_args()
    engine = create_engine(os.environ["OPERATIONS_MIGRATION_DATABASE_URI"])
    try:
        with engine.begin() as connection:
            upgrade(connection, args.schema)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
