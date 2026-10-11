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
"""Disposable schemas; fixture DDL is never part of runtime code."""

import os
from collections.abc import Iterator
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, Engine
from sqlalchemy.schema import CreateSchema, DropSchema

from superset.operations.tables import OperationsTables


@pytest.fixture(scope="module")
def operations_engine() -> Iterator[Engine]:
    """Connect only to an explicitly supplied PostgreSQL test database."""
    uri = os.environ.get("OPERATIONS_TEST_DATABASE_URI")
    if not uri:
        pytest.skip("Set OPERATIONS_TEST_DATABASE_URI for PostgreSQL persistence tests")
    engine = create_engine(uri)
    assert engine.dialect.name == "postgresql"
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def operations_tables(operations_engine: Engine) -> Iterator[OperationsTables]:
    """Create and remove only the unique schema allocated by this fixture."""
    schema = f"operations_test_{uuid4().hex}"
    tables = OperationsTables(schema)
    with operations_engine.begin() as connection:
        connection.execute(CreateSchema(schema))
        tables.metadata.create_all(connection)
    try:
        yield tables
    finally:
        with operations_engine.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
