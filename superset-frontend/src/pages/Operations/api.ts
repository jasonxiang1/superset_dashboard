/**
 * Licensed to the Apache Software Foundation (ASF) under one
 * or more contributor license agreements.  See the NOTICE file
 * distributed with this work for additional information
 * regarding copyright ownership.  The ASF licenses this file
 * to you under the Apache License, Version 2.0 (the
 * "License"); you may not use this file except in compliance
 * with the License.  You may obtain a copy of the License at
 *
 *   http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing,
 * software distributed under the License is distributed on an
 * "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
 * KIND, either express or implied.  See the License for the
 * specific language governing permissions and limitations
 * under the License.
 */

import { SupersetClient } from '@superset-ui/core';
import { t } from '@apache-superset/core/translation';
import {
  RecordMap,
  Resource,
  WritableResource,
  WritableRecord,
  Settings,
  Brief,
  HistoryEntry,
} from './types';

const root = '/api/v1/operations';
const prefix = (workspace: string) =>
  `${root}/workspaces/${encodeURIComponent(workspace)}`;

/** Read a typed Operations result using Superset's authenticated client. */
async function getResult<T>(
  endpoint: string,
  signal?: AbortSignal,
): Promise<T> {
  const { json } = await SupersetClient.get({ endpoint, signal });
  return json.result as T;
}
export const getSettings = (signal?: AbortSignal) =>
  getResult<Settings>(`${root}/settings`, signal);
export const getBrief = (workspace: string, signal?: AbortSignal) =>
  getResult<Brief>(`${prefix(workspace)}/brief`, signal);
export const getRecord = <R extends Resource>(
  workspace: string,
  resource: R,
  id: string,
) =>
  getResult<RecordMap[R]>(
    `${prefix(workspace)}/${resource}/${encodeURIComponent(id)}`,
  );
export const getHistory = (
  workspace: string,
  resource: WritableResource,
  id: string,
  offset = 0,
) =>
  getResult<HistoryEntry[]>(
    `${prefix(workspace)}/${resource}/${encodeURIComponent(id)}/history?offset=${offset}`,
  );
export const listRecords = <R extends Resource>(
  workspace: string,
  resource: R,
  options: {
    projectId?: string;
    archived?: boolean;
    offset?: number;
    summaryDate?: string;
    summaryId?: string;
    limit?: number;
  } = {},
  signal?: AbortSignal,
) => {
  const query = new URLSearchParams({
    limit: String(options.limit || 100),
    offset: String(options.offset || 0),
    include_archived: String(Boolean(options.archived)),
  });
  if (options.summaryId) query.set('summary_id', options.summaryId);
  if (options.projectId) query.set('project_id', options.projectId);
  if (options.summaryDate) query.set('summary_date', options.summaryDate);
  return getResult<RecordMap[R][]>(
    `${prefix(workspace)}/${resource}?${query}`,
    signal,
  );
};
/** Mutations have no automatic retry; creation retries retain their request UUID. */
export async function saveRecord(
  workspace: string,
  resource: WritableResource,
  payload: Record<string, unknown>,
  id?: string,
): Promise<WritableRecord> {
  const { json } = await SupersetClient.request({
    endpoint: `${prefix(workspace)}/${resource}${id ? `/${encodeURIComponent(id)}` : ''}`,
    method: id ? 'PATCH' : 'POST',
    jsonPayload: payload,
    fetchRetryOptions: { retries: 0, retryOn: [] },
  });
  return json.result as WritableRecord;
}
export async function changeRecord(
  workspace: string,
  resource: WritableResource,
  record: WritableRecord,
  action: 'archive' | 'restore' | 'completion',
  completed?: boolean,
) {
  const { json } = await SupersetClient.post({
    endpoint: `${prefix(workspace)}/${resource}/${encodeURIComponent(record.id)}/${action}`,
    jsonPayload: {
      revision: record.revision,
      ...(action === 'completion' ? { completed } : {}),
    },
    fetchRetryOptions: { retries: 0, retryOn: [] },
  });
  return json.result as WritableRecord;
}
/** Extract API messages without assuming every failure is an HTTP response. */
export async function describeError(
  error: unknown,
): Promise<{ message: string; status?: number }> {
  if (typeof error === 'object' && error !== null) {
    const response = error as {
      status?: number;
      json?: () => Promise<{
        message?: string;
        errors?: Record<string, string[]>;
      }>;
    };
    if (typeof response.json === 'function') {
      try {
        const body = await response.json();
        return {
          status: response.status,
          message:
            [body.message, ...Object.values(body.errors || {}).flat()]
              .filter(Boolean)
              .join(' ') || t('Could not save or load records. Please retry.'),
        };
      } catch {
        /* A failed response may not contain JSON. */
      }
    }
    if (error instanceof Error)
      return { message: error.message, status: response.status };
  }
  return { message: t('Could not save or load records. Please retry.') };
}
/** Traverse explicit pages so status groups and worker selectors are complete. */
export async function listAll<R extends Resource>(
  workspace: string,
  resource: R,
  options: {
    projectId?: string;
    archived?: boolean;
    summaryDate?: string;
    summaryId?: string;
    limit?: number;
  } = {},
  signal?: AbortSignal,
): Promise<RecordMap[R][]> {
  const result: RecordMap[R][] = [];
  for (let offset = 0; offset <= 100000; offset += 100) {
    const page = await listRecords(
      workspace,
      resource,
      { ...options, offset },
      signal,
    );
    result.push(...page);
    if (page.length < 100) return result;
  }
  throw new Error(t('Too many records to load. Narrow the workspace scope.'));
}
