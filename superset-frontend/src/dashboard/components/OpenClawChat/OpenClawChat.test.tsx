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
import { configureStore } from '@reduxjs/toolkit';
import { SupersetClient } from '@superset-ui/core';
import {
  fireEvent,
  render,
  screen,
  waitFor,
} from 'spec/helpers/testing-library';
import OpenClawChat, { getDashboardContext } from '.';

const context = {
  sliceEntities: {
    slices: {
      9: {
        slice_id: 9,
        slice_name: 'Revenue',
        description: 'Monthly sales',
        viz_type: 'echarts_timeseries_bar',
        form_data: {
          datasource: '3__table',
          metrics: ['revenue'],
          groupby: ['month'],
        },
      },
    },
  },
  datasources: {
    '3__table': {
      table_name: 'sales',
      metrics: [{ metric_name: 'revenue', expression: 'SUM(amount)' }],
      columns: [{ column_name: 'month', description: 'Sale month' }],
      database: { password: 'must-not-be-sent' },
    },
    '99__table': { table_name: 'unrelated' },
  },
  nativeFilters: { filters: {} },
  dataMask: {
    region: {
      filterState: { value: ['West'] },
      extraFormData: { filters: [{ col: 'region', op: 'IN', val: ['West'] }] },
      ownState: { clientView: { rows: [{ secret_row: 'must-not-be-sent' }] } },
    },
  },
  dashboardFilters: {},
} as unknown as Parameters<typeof getDashboardContext>[0];
const dashboard = { dashboardId: 4, dashboardTitle: 'Sales overview' };

function setup() {
  const store = configureStore({
    reducer: (state = context, action) =>
      action.type === 'changeFilters'
        ? {
            ...state,
            dataMask: { region: { filterState: { value: ['East'] } } },
          }
        : state,
  });
  const view = render(<OpenClawChat {...dashboard} />, {
    useRedux: true,
    store,
  });
  fireEvent.click(screen.getByRole('button', { name: 'Ask Jason' }));
  return { ...view, store };
}

async function send(question: string) {
  fireEvent.change(
    await screen.findByRole('textbox', { name: 'Message to Jason' }),
    {
      target: { value: question },
    },
  );
  fireEvent.click(screen.getByRole('button', { name: 'Send' }));
}

afterEach(() => jest.restoreAllMocks());

test('context includes chart definitions and selection without database credentials', () => {
  const result = getDashboardContext(context, dashboard, 9);
  expect(result.selectedChart).toBe(9);
  expect(result.charts[0].configuration).toEqual({
    datasource: '3__table',
    metrics: ['revenue'],
    groupby: ['month'],
  });
  expect(result.datasets).toHaveLength(1);
  expect(result.charts[0].dataset).toBe('3__table');
  expect(result.filterState.region).toEqual({
    filterState: { value: ['West'] },
    extraFormData: { filters: [{ col: 'region', op: 'IN', val: ['West'] }] },
  });
  expect(result.datasets[0].metrics?.[0].expression).toBe('SUM(amount)');
  expect(JSON.stringify(result)).not.toContain('must-not-be-sent');
  expect(JSON.stringify(result)).not.toContain('unrelated');
});

test('disconnected message preserves the draft and allows an explicit retry', async () => {
  const post = jest
    .spyOn(SupersetClient, 'post')
    .mockRejectedValue(new Error('offline'));
  setup();
  await send('Explain revenue');
  expect(await screen.findByRole('alert')).toHaveTextContent(
    'Cannot connect to OpenClaw right now.',
  );
  expect(screen.getByRole('textbox')).toHaveValue('Explain revenue');
  expect(post).toHaveBeenCalledTimes(1);

  post.mockResolvedValue({
    json: { result: 'Revenue is total sales.' },
    response: new Response(),
  });
  fireEvent.click(screen.getByRole('button', { name: 'Send' }));
  expect(
    await screen.findByText('Revenue is total sales.'),
  ).toBeInTheDocument();
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  expect(screen.getByRole('textbox')).toHaveValue('');
  expect(post).toHaveBeenCalledTimes(2);
});

test('follow-ups include fresh filters and new conversations use a new session', async () => {
  const post = jest.spyOn(SupersetClient, 'post').mockResolvedValue({
    json: { result: 'Revenue is total sales.' },
    response: new Response(),
  });
  const { store } = setup();
  await send('Explain revenue');
  await screen.findByText('Revenue is total sales.');
  const [[first]] = post.mock.calls;
  expect(first.fetchRetryOptions).toEqual({ retries: 0, retryOn: [] });
  expect(first.jsonPayload).toMatchObject({
    context: { filterState: { region: { filterState: { value: ['West'] } } } },
  });
  store.dispatch({ type: 'changeFilters' });
  await send('What about this region?');
  await waitFor(() => expect(screen.getByRole('textbox')).toHaveValue(''));
  expect(post.mock.calls[1][0].jsonPayload).toMatchObject({
    conversation_id: (first.jsonPayload as Record<string, unknown>)
      .conversation_id,
    context: { filterState: { region: { filterState: { value: ['East'] } } } },
  });

  fireEvent.click(screen.getByRole('button', { name: 'New conversation' }));
  expect(screen.queryByText('Explain revenue')).not.toBeInTheDocument();
  await send('Start again');
  await waitFor(() => expect(post).toHaveBeenCalledTimes(3));
  expect(post.mock.calls[2][0].jsonPayload).not.toMatchObject({
    conversation_id: (first.jsonPayload as Record<string, unknown>)
      .conversation_id,
  });
});

test('closing and reopening retains history and renders agent text safely', async () => {
  jest.spyOn(SupersetClient, 'post').mockResolvedValue({
    json: { result: '<script>unsafe()</script>' },
    response: new Response(),
  });
  setup();
  await send('Explain');
  const text = await screen.findByText('<script>unsafe()</script>');
  expect(text.querySelector('script')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Close' }));
  fireEvent.click(screen.getByRole('button', { name: 'Ask Jason' }));
  expect(
    await screen.findByText('<script>unsafe()</script>'),
  ).toBeInTheDocument();
});

test('an in-flight message cannot be sent twice and aborts on unmount', async () => {
  const post = jest
    .spyOn(SupersetClient, 'post')
    .mockReturnValue(new Promise(() => {}));
  const { unmount } = setup();
  await send('Explain');
  expect(
    screen.getByRole('button', { name: 'New conversation' }),
  ).toBeDisabled();
  expect(screen.getByRole('textbox')).toBeDisabled();
  expect(post).toHaveBeenCalledTimes(1);
  const { signal } = post.mock.calls[0][0];
  unmount();
  expect(signal?.aborted).toBe(true);
});
