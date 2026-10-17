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
import {
  render,
  screen,
  userEvent,
  waitFor,
} from 'spec/helpers/testing-library';
import OperationsChat from './OperationsChat';
import { previewLink } from './Attachments';

jest.mock('@superset-ui/core', () => ({
  ...jest.requireActual('@superset-ui/core'),
  SupersetClient: { post: jest.fn() },
}));

beforeEach(() => {
  jest.clearAllMocks();
});

test('previews reject executable URLs and normalize supported Drive documents', () => {
  // eslint-disable-next-line no-script-url -- verifies rejecting executable input
  expect(previewLink('javascript:alert(1)')).toBeUndefined();
  expect(
    previewLink('https://user:password@example.com/a.pdf'),
  ).toBeUndefined();
  expect(previewLink('https://example.com/a.html')).toBeUndefined();
  expect(
    previewLink('https://drive.google.com/file/d/abc123/view?usp=sharing'),
  ).toEqual({
    url: 'https://drive.google.com/file/d/abc123/preview',
    image: false,
  });
  expect(previewLink('https://example.com/photo.png')).toEqual({
    url: 'https://example.com/photo.png',
    image: true,
  });
});

test('disconnected chat preserves draft and never retries automatically', async () => {
  jest
    .mocked(SupersetClient.post)
    .mockRejectedValue(new Error('Check the connection'));
  render(
    <OperationsChat
      workspace="workspace"
      view="daily_brief"
      onChanged={jest.fn()}
    />,
    { useTheme: true },
  );
  await userEvent.click(screen.getByText('Ask Jason'));
  await userEvent.type(
    screen.getByLabelText('Message Jason'),
    'What happened?',
  );
  await userEvent.click(screen.getByText('Send'));
  expect(await screen.findByRole('alert')).toHaveTextContent(
    'Check the connection',
  );
  expect(screen.getByLabelText('Message Jason')).toHaveValue('What happened?');
  expect(SupersetClient.post).toHaveBeenCalledTimes(1);
});

test('proposals wait for Apply, keep history and reset conversation explicitly', async () => {
  jest.mocked(SupersetClient.post).mockResolvedValue({
    json: {
      result: {
        reply: '<script>unsafe()</script>',
        proposal: { id: 'proposal-1', changes: ['Rename the project'] },
      },
    },
  } as unknown as Awaited<ReturnType<typeof SupersetClient.post>>);
  const onChanged = jest.fn();
  render(
    <OperationsChat
      workspace="workspace"
      view="projects"
      onChanged={onChanged}
    />,
    { useTheme: true },
  );
  await userEvent.click(screen.getByText('Ask Jason'));
  await userEvent.type(screen.getByLabelText('Message Jason'), 'Rename it');
  await userEvent.click(screen.getByText('Send'));
  expect(await screen.findByText('<script>unsafe()</script>')).toBeVisible();
  expect(document.querySelector('script')).toBeNull();
  expect(SupersetClient.post).toHaveBeenCalledTimes(1);
  const first = jest.mocked(SupersetClient.post).mock.calls[0][0];
  await userEvent.click(screen.getByText('Apply'));
  await waitFor(() => expect(onChanged).toHaveBeenCalledTimes(1));
  expect(jest.mocked(SupersetClient.post).mock.calls[1][0]).toMatchObject({
    endpoint:
      '/api/v1/operations/workspaces/workspace/chat/proposals/proposal-1',
    jsonPayload: {
      decision: 'apply',
      conversation_id: (first.jsonPayload as Record<string, unknown>)
        .conversation_id,
    },
  });
  await userEvent.click(screen.getByText('New conversation'));
  expect(screen.queryByText('Rename the project')).not.toBeInTheDocument();
});

test('pending request disables sends and unmount aborts the browser request', async () => {
  jest
    .mocked(SupersetClient.post)
    .mockImplementation(() => new Promise(() => {}));
  const { unmount } = render(
    <OperationsChat
      workspace="workspace"
      view="projects"
      onChanged={jest.fn()}
    />,
    { useTheme: true },
  );
  await userEvent.click(screen.getByText('Ask Jason'));
  await userEvent.type(screen.getByLabelText('Message Jason'), 'Inspect');
  await userEvent.click(screen.getByText('Send'));
  expect(screen.getByText('Send').closest('button')).toBeDisabled();
  const { signal } = jest.mocked(SupersetClient.post).mock.calls[0][0];
  unmount();
  expect(signal?.aborted).toBe(true);
  expect(SupersetClient.post).toHaveBeenCalledTimes(1);
});
