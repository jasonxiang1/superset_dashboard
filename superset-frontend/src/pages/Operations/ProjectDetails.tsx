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

import { useEffect, useState } from 'react';
import { t } from '@apache-superset/core/translation';
import {
  Button,
  Checkbox,
  Flex,
  Loading,
  Switch,
  Typography,
} from '@superset-ui/core/components';
import { changeRecord, describeError, listAll } from './api';
import {
  Assignment,
  Capabilities,
  DialogState,
  LaborEntry,
  Project,
  ProjectNote,
  Task,
  Worker,
} from './types';
import RecordActions from './RecordActions';
import Attachments from './Attachments';

interface Props {
  project: Project;
  workspace: string;
  workers: Worker[];
  capabilities: Capabilities;
  refreshToken: number;
  onEdit: (dialog: DialogState) => void;
  onChanged: () => void;
}
interface Details {
  tasks: Task[];
  notes: ProjectNote[];
  labor: LaborEntry[];
  assignments: Assignment[];
}
/** Load scoped project children; refresh after writes without collapsing the row. */
export default function ProjectDetails({
  project,
  workspace,
  workers,
  capabilities,
  refreshToken,
  onEdit,
  onChanged,
}: Props) {
  const [details, setDetails] = useState<Details>();
  const [removed, setRemoved] = useState(false);
  const [error, setError] = useState('');
  const [pendingTask, setPendingTask] = useState<string>();
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError('');
    const options = {
      projectId: project.id,
      archived: removed || Boolean(project.archived_at),
    };
    Promise.all([
      listAll(workspace, 'tasks', options, controller.signal),
      listAll(workspace, 'notes', options, controller.signal),
      listAll(workspace, 'labor_entries', options, controller.signal),
      listAll(workspace, 'assignments', options, controller.signal),
    ])
      .then(([tasks, notes, labor, assignments]) => {
        if (!controller.signal.aborted)
          setDetails({ tasks, notes, labor, assignments });
      })
      .catch(async failure => {
        const detail = await describeError(failure);
        if (!controller.signal.aborted) setError(detail.message);
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [workspace, project.id, project.archived_at, removed, refreshToken]);
  const name = (id: string) =>
    workers.find(worker => worker.id === id)?.name || id;
  const complete = async (task: Task) => {
    setPendingTask(task.id);
    try {
      await changeRecord(
        workspace,
        'tasks',
        task,
        'completion',
        !task.completed,
      );
      onChanged();
    } catch (failure) {
      setError((await describeError(failure)).message);
    } finally {
      setPendingTask(undefined);
    }
  };
  const actions = (
    resource: 'tasks' | 'notes' | 'labor_entries' | 'assignments',
    record: Task | ProjectNote | LaborEntry | Assignment,
  ) =>
    !project.archived_at && (
      <RecordActions
        resource={resource}
        record={record}
        workspace={workspace}
        capabilities={capabilities}
        onEdit={onEdit}
        onChanged={onChanged}
      />
    );
  if (loading) return <Loading />;
  return (
    <Flex vertical gap="middle">
      {error && (
        <div role="alert">
          <Typography.Text type="danger">{error}</Typography.Text>
          <Button onClick={onChanged}>{t('Reload records')}</Button>
        </div>
      )}
      {project.description && (
        <Typography.Paragraph>{project.description}</Typography.Paragraph>
      )}
      <RecordActions
        resource="projects"
        record={project}
        workspace={workspace}
        capabilities={capabilities}
        onEdit={onEdit}
        onChanged={onChanged}
      />
      <Flex gap="small" wrap align="center">
        {!project.archived_at && (
          <>
            {capabilities.create && (
              <Button
                size="small"
                onClick={() =>
                  onEdit({ resource: 'tasks', projectId: project.id })
                }
              >
                {t('Add task')}
              </Button>
            )}
            {capabilities.log_labor && (
              <Button
                size="small"
                onClick={() =>
                  onEdit({ resource: 'labor_entries', projectId: project.id })
                }
              >
                {t('Log hours')}
              </Button>
            )}
            {capabilities.add_note && (
              <Button
                size="small"
                onClick={() =>
                  onEdit({ resource: 'notes', projectId: project.id })
                }
              >
                {t('Add note')}
              </Button>
            )}
            {capabilities.assign && (
              <Button
                size="small"
                onClick={() =>
                  onEdit({ resource: 'assignments', projectId: project.id })
                }
              >
                {t('Assign worker')}
              </Button>
            )}
          </>
        )}
        <Switch
          checked={removed}
          onChange={setRemoved}
          aria-label={t('Include removed project records')}
        />
        <Typography.Text>{t('Include removed records')}</Typography.Text>
      </Flex>
      {details && (
        <>
          <Typography.Title level={5}>{t('Workers')}</Typography.Title>
          {details.assignments.length === 0 && (
            <Typography.Text type="secondary">
              {t('No assigned workers')}
            </Typography.Text>
          )}
          {details.assignments.map(assignment => (
            <Flex
              data-operations-resource="assignments"
              data-operations-record={assignment.id}
              key={assignment.id}
              justify="space-between"
              gap="small"
              wrap
            >
              <Typography.Text delete={Boolean(assignment.archived_at)}>
                {name(assignment.worker_id)}
              </Typography.Text>
              {actions('assignments', assignment)}
            </Flex>
          ))}
          <Typography.Title level={5}>{t('Tasks')}</Typography.Title>
          {details.tasks.length === 0 && (
            <Typography.Text type="secondary">{t('No tasks')}</Typography.Text>
          )}
          {details.tasks.map(task => (
            <Flex
              data-operations-resource="tasks"
              data-operations-record={task.id}
              key={task.id}
              justify="space-between"
              gap="small"
              wrap
            >
              <Flex vertical>
                <Checkbox
                  checked={task.completed}
                  disabled={
                    !capabilities.complete ||
                    Boolean(task.archived_at) ||
                    Boolean(project.archived_at) ||
                    Boolean(pendingTask)
                  }
                  onChange={() => complete(task)}
                >
                  {task.title}
                </Checkbox>
                <Typography.Text type="secondary">
                  {[
                    task.assignee_name,
                    task.due_at && new Date(task.due_at).toLocaleString(),
                  ]
                    .filter(Boolean)
                    .join(' · ')}
                </Typography.Text>
              </Flex>
              {actions('tasks', task)}
            </Flex>
          ))}
          <Typography.Title level={5}>{t('Labor hours')}</Typography.Title>
          {details.labor.length === 0 && (
            <Typography.Text type="secondary">
              {t('No labor entries')}
            </Typography.Text>
          )}
          {details.labor.map(entry => (
            <Flex
              data-operations-resource="labor_entries"
              data-operations-record={entry.id}
              key={entry.id}
              vertical
            >
              <Flex justify="space-between" gap="small" wrap>
                <Typography.Text delete={Boolean(entry.archived_at)}>
                  {name(entry.worker_id)} · {entry.work_date} ·{' '}
                  {(entry.minutes / 60).toFixed(2)} {t('hours')}
                </Typography.Text>
                {actions('labor_entries', entry)}
              </Flex>
              {entry.note && (
                <Typography.Paragraph>{entry.note}</Typography.Paragraph>
              )}
            </Flex>
          ))}
          <Typography.Title level={5}>{t('Notes')}</Typography.Title>
          {details.notes.length === 0 && (
            <Typography.Text type="secondary">
              {t('No project notes')}
            </Typography.Text>
          )}
          {details.notes.map(note => (
            <Flex
              data-operations-resource="notes"
              data-operations-record={note.id}
              key={note.id}
              vertical
            >
              <Typography.Paragraph delete={Boolean(note.archived_at)}>
                {note.body}
              </Typography.Paragraph>
              <Flex justify="space-between" gap="small" wrap>
                <Typography.Text type="secondary">
                  {new Date(note.created_at).toLocaleString()}
                </Typography.Text>
                {actions('notes', note)}
              </Flex>
            </Flex>
          ))}
          <Attachments
            workspace={workspace}
            projectId={project.id}
            archived={Boolean(project.archived_at)}
            capabilities={capabilities}
            refreshToken={refreshToken}
            onChanged={onChanged}
          />
        </>
      )}
    </Flex>
  );
}
