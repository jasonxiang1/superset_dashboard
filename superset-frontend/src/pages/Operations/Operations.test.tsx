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

import { MemoryRouter } from 'react-router-dom';
import {
  render,
  screen,
  waitFor,
  userEvent,
  within,
} from 'spec/helpers/testing-library';
import Operations from '.';
import RecordForm from './RecordForm';
import {
  getSettings,
  getBrief,
  listAll,
  changeRecord,
  saveRecord,
  getRecord,
} from './api';
import { Brief, Project, RecordMap, Resource, Settings, Task } from './types';

jest.mock('./api', () => ({
  ...jest.requireActual<typeof import('./api')>('./api'),
  getSettings: jest.fn(),
  getBrief: jest.fn(),
  listAll: jest.fn(),
  changeRecord: jest.fn(),
  saveRecord: jest.fn(),
  getRecord: jest.fn(),
  getHistory: jest.fn(),
}));
const base = {
  id: 'project-1',
  workspace_id: 'workspace-1',
  revision: 1,
  created_at: '2026-10-10T12:00:00Z',
  updated_at: '2026-10-10T12:00:00Z',
  archived_at: null,
};
const project: Project = {
  ...base,
  title: 'Kitchen remodel',
  description: '',
  status: 'in_progress',
};
const task: Task = {
  ...base,
  id: 'task-1',
  project_id: project.id,
  title: 'Call client',
  description: '',
  assignee_name: 'Alex',
  due_at: '2026-10-10T18:00:00Z',
  completed: false,
  completed_at: null,
};
const settings: Settings = {
  workspaces: [{ id: 'workspace-1', name: 'Workshop' }],
  capabilities: {
    log_labor: true,
    add_note: true,
    assign: true,
    create: true,
    edit: true,
    complete: true,
    remove: true,
    restore: true,
  },
  timezone: 'UTC',
  currency: 'USD',
};
const brief: Brief = {
  day: '2026-10-10',
  timezone: 'UTC',
  currency: 'USD',
  items_today: 1,
  completed_today: 0,
  open_jobs: 1,
  tasks: [task],
  tasks_has_more: false,
  labor_due: null,
  margin_percent: null,
  financial_period: 'all_recorded_work',
  project_financials: {},
};

beforeEach(() => {
  jest.clearAllMocks();
  window.featureFlags = { OPERATIONS_ENABLED: true };
  jest.mocked(getSettings).mockResolvedValue(settings);
  jest.mocked(getBrief).mockResolvedValue(brief);
  const records: { [R in Resource]: RecordMap[R][] } = {
    projects: [project],
    tasks: [task],
    notes: [],
    assignments: [],
    labor_entries: [],
    workers: [],
    attachments: [],
  };
  jest
    .mocked(listAll)
    .mockImplementation(async (_workspace, resource) => records[resource]);
  jest
    .mocked(changeRecord)
    .mockResolvedValue({ ...task, completed: true, revision: 2 });
  jest.mocked(saveRecord).mockResolvedValue(project);
});
function renderPage(path = '/operations/brief/') {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Operations />
    </MemoryRouter>,
    { useTheme: true },
  );
}

test('daily brief uses real metrics and persists completion before refreshing', async () => {
  renderPage();
  const checkbox = await screen.findByRole('checkbox', { name: 'Call client' });
  expect(screen.getAllByText('Unavailable')).toHaveLength(2);
  await userEvent.click(checkbox);
  await waitFor(() =>
    expect(changeRecord).toHaveBeenCalledWith(
      'workspace-1',
      'tasks',
      task,
      'completion',
      true,
    ),
  );
  await waitFor(() => expect(getBrief).toHaveBeenCalledTimes(2));
});

test('projects navigate into status bins and expandable details without graphs', async () => {
  renderPage();
  await screen.findByRole('checkbox', { name: 'Call client' });
  await userEvent.click(screen.getByRole('button', { name: 'Projects' }));
  await userEvent.click(await screen.findByText('In progress (1)'));
  await userEvent.click(await screen.findByText('Kitchen remodel'));
  expect(
    await screen.findByRole('button', { name: 'Log hours' }),
  ).toBeVisible();
  expect(screen.getByRole('button', { name: 'Add note' })).toBeVisible();
});

test('read-only users have no mutation controls', async () => {
  jest.mocked(getSettings).mockResolvedValue({
    ...settings,
    capabilities: {
      log_labor: false,
      add_note: false,
      assign: false,
      create: false,
      edit: false,
      complete: false,
      remove: false,
      restore: false,
    },
  });
  renderPage();
  expect(
    await screen.findByRole('checkbox', { name: 'Call client' }),
  ).toBeDisabled();
  expect(
    screen.queryByRole('button', { name: 'Add task' }),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByRole('button', { name: 'Edit' }),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByRole('button', { name: 'History' }),
  ).not.toBeInTheDocument();
});

test('removal names the record and only archives after confirmation', async () => {
  renderPage();
  await screen.findByRole('checkbox', { name: 'Call client' });
  await userEvent.click(screen.getByRole('button', { name: 'Remove' }));
  const modal = await screen.findByRole('dialog');
  await waitFor(() =>
    expect(within(modal).getByText(/Call client/)).toBeVisible(),
  );
  expect(changeRecord).not.toHaveBeenCalled();
  await userEvent.click(within(modal).getByRole('button', { name: 'Remove' }));
  await waitFor(() =>
    expect(changeRecord).toHaveBeenCalledWith(
      'workspace-1',
      'tasks',
      task,
      'archive',
    ),
  );
});

test('task edits send only mutable fields and the displayed revision', async () => {
  render(
    <RecordForm
      dialog={{ resource: 'tasks', record: task }}
      workspace="workspace-1"
      projects={[project]}
      workers={[]}
      day={brief.day}
      onClose={jest.fn()}
      onSaved={jest.fn()}
    />,
    { useTheme: true },
  );
  const input = screen.getByRole('textbox', { name: 'Title' });
  await userEvent.clear(input);
  await userEvent.type(input, 'Confirm client');
  await userEvent.click(screen.getByRole('button', { name: 'Save' }));
  await waitFor(() => expect(saveRecord).toHaveBeenCalled());
  const [, , payload, id] = jest.mocked(saveRecord).mock.calls[0];
  expect(id).toBe(task.id);
  expect(payload).toMatchObject({ title: 'Confirm client', revision: 1 });
  expect(payload).not.toHaveProperty('completed');
  expect(payload).not.toHaveProperty('id');
  expect(payload).not.toHaveProperty('workspace_id');
  expect(payload).not.toHaveProperty('status');
});

test('ambiguous note creation retries the identical payload and request UUID', async () => {
  jest
    .mocked(saveRecord)
    .mockRejectedValueOnce(new Error('Connection interrupted'))
    .mockResolvedValueOnce(project);
  const onSaved = jest.fn();
  render(
    <RecordForm
      dialog={{ resource: 'notes', projectId: project.id }}
      workspace="workspace-1"
      projects={[project]}
      workers={[]}
      day={brief.day}
      onClose={jest.fn()}
      onSaved={onSaved}
    />,
    { useTheme: true },
  );
  await userEvent.type(
    screen.getByRole('textbox', { name: 'Note' }),
    'Client approved finish',
  );
  await userEvent.click(screen.getByRole('button', { name: 'Save' }));
  const retry = await screen.findByRole('button', { name: 'Retry submission' });
  expect(screen.getByRole('textbox', { name: 'Note' })).toBeDisabled();
  await userEvent.click(retry);
  await waitFor(() => expect(onSaved).toHaveBeenCalled());
  expect(jest.mocked(saveRecord).mock.calls[0][2]).toEqual(
    jest.mocked(saveRecord).mock.calls[1][2],
  );
  expect(jest.mocked(saveRecord).mock.calls[0][2]).toMatchObject({
    body: 'Client approved finish',
    project_id: project.id,
  });
  expect(jest.mocked(saveRecord).mock.calls[0][2]).not.toHaveProperty(
    'description',
  );
});

test('conflicting edits keep the draft until explicit reload of the saved revision', async () => {
  jest
    .mocked(saveRecord)
    .mockRejectedValueOnce({
      status: 409,
      json: async () => ({ message: 'Record changed. Reload before saving.' }),
    })
    .mockResolvedValueOnce(project);
  jest.mocked(getRecord).mockResolvedValue({
    ...project,
    title: 'Another editor title',
    revision: 2,
  });
  render(
    <RecordForm
      dialog={{ resource: 'projects', record: project }}
      workspace="workspace-1"
      projects={[project]}
      workers={[]}
      day={brief.day}
      onClose={jest.fn()}
      onSaved={jest.fn()}
    />,
    { useTheme: true },
  );
  await userEvent.type(
    screen.getByRole('textbox', { name: 'Title' }),
    ' draft',
  );
  await userEvent.click(screen.getByRole('button', { name: 'Save' }));
  await screen.findByRole('alert');
  expect(screen.getByRole('textbox', { name: 'Title' })).toHaveValue(
    'Kitchen remodel draft',
  );
  await userEvent.click(
    screen.getByRole('button', { name: 'Reload saved record' }),
  );
  await waitFor(() =>
    expect(screen.getByRole('textbox', { name: 'Title' })).toHaveValue(
      'Another editor title',
    ),
  );
  await userEvent.click(screen.getByRole('button', { name: 'Save' }));
  await waitFor(() => expect(saveRecord).toHaveBeenCalledTimes(2));
  expect(jest.mocked(saveRecord).mock.calls[1][2]).toMatchObject({
    revision: 2,
  });
});

test('late responses from another workspace cannot replace the selected data', async () => {
  jest.mocked(getSettings).mockResolvedValue({
    ...settings,
    workspaces: [
      { id: 'workspace-1', name: 'Workshop' },
      { id: 'workspace-2', name: 'Office' },
    ],
  });
  let firstResolve: (value: Brief) => void = () => {};
  jest.mocked(getBrief).mockImplementation(workspace =>
    workspace === 'workspace-1'
      ? new Promise(resolve => {
          firstResolve = resolve;
        })
      : Promise.resolve({ ...brief, open_jobs: 8, tasks: [] }),
  );
  const { rerender } = render(
    <MemoryRouter initialEntries={['/operations/brief/?workspace=workspace-1']}>
      <Operations />
    </MemoryRouter>,
    { useTheme: true },
  );
  await waitFor(() => expect(getBrief).toHaveBeenCalled());
  rerender(
    <MemoryRouter
      key="second"
      initialEntries={['/operations/brief/?workspace=workspace-2']}
    >
      <Operations />
    </MemoryRouter>,
  );
  await screen.findByText('No tasks due today');
  firstResolve(brief);
  expect(
    screen.queryByRole('checkbox', { name: 'Call client' }),
  ).not.toBeInTheDocument();
});
