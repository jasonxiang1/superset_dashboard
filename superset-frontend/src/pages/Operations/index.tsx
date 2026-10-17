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
import { useHistory, useLocation } from 'react-router-dom';
import { FeatureFlag, isFeatureEnabled } from '@superset-ui/core';
import { useTheme } from '@apache-superset/core/theme';
import { t } from '@apache-superset/core/translation';
import {
  Button,
  Card,
  Checkbox,
  Collapse,
  Col,
  Flex,
  Loading,
  Progress,
  Row,
  Select,
  Switch,
  Tooltip,
  Typography,
} from '@superset-ui/core/components';
import { RoutePaths } from 'src/views/routePaths';
import {
  changeRecord,
  describeError,
  getBrief,
  getSettings,
  listAll,
} from './api';
import {
  Assignment,
  Brief,
  DialogState,
  Project,
  ProjectStatus,
  Settings,
  Task,
  Worker,
} from './types';
import RecordForm, { statusLabels } from './RecordForm';
import RecordActions from './RecordActions';
import ProjectDetails from './ProjectDetails';
import SummaryUpdates from './SummaryUpdates';
import OperationsChat from './OperationsChat';

interface Data {
  workspace: string;
  brief: Brief;
  projects: Project[];
  workers: Worker[];
  assignments: Assignment[];
}
/** Fixed-layout business operations, added alongside Superset dashboards. */
export default function Operations() {
  const theme = useTheme();
  const location = useLocation();
  const history = useHistory();
  const [settings, setSettings] = useState<Settings>();
  const [data, setData] = useState<Data>();
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [removed, setRemoved] = useState(false);
  const [removedWorkers, setRemovedWorkers] = useState(false);
  const [refreshToken, setRefreshToken] = useState(0);
  const [dialog, setDialog] = useState<DialogState>();
  const [pendingTask, setPendingTask] = useState<string>();
  const projectsView = location.pathname === RoutePaths.OPERATIONS_PROJECTS;
  const requestedWorkspace = new URLSearchParams(location.search).get(
    'workspace',
  );
  const selected = requestedWorkspace
    ? settings?.workspaces.find(item => item.id === requestedWorkspace)
    : settings?.workspaces[0];
  const workspace = selected?.id;
  const enabled = isFeatureEnabled(FeatureFlag.Operations);
  const refresh = () => setRefreshToken(value => value + 1);

  useEffect(() => {
    if (!enabled) return undefined;
    const controller = new AbortController();
    getSettings(controller.signal)
      .then(value => {
        if (!controller.signal.aborted) setSettings(value);
      })
      .catch(async failure => {
        const detail = await describeError(failure);
        if (!controller.signal.aborted) setError(detail.message);
      });
    return () => controller.abort();
  }, [enabled, refreshToken]);
  useEffect(() => {
    if (!workspace) return undefined;
    const controller = new AbortController();
    setLoading(true);
    setError('');
    setDialog(undefined);
    Promise.all([
      getBrief(workspace, controller.signal),
      listAll(workspace, 'projects', { archived: removed }, controller.signal),
      listAll(workspace, 'workers', { archived: true }, controller.signal),
      listAll(workspace, 'assignments', {}, controller.signal),
    ])
      .then(([brief, projects, workers, assignments]) => {
        if (!controller.signal.aborted)
          setData({ workspace, brief, projects, workers, assignments });
      })
      .catch(async failure => {
        const detail = await describeError(failure);
        if (!controller.signal.aborted) setError(detail.message);
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [workspace, removed, refreshToken]);
  const complete = async (task: Task) => {
    if (!workspace) return;
    setPendingTask(task.id);
    try {
      await changeRecord(
        workspace,
        'tasks',
        task,
        'completion',
        !task.completed,
      );
      refresh();
    } catch (failure) {
      setError((await describeError(failure)).message);
    } finally {
      setPendingTask(undefined);
    }
  };
  if (!enabled)
    return (
      <Typography.Paragraph>
        {t('Operations is disabled.')}
      </Typography.Paragraph>
    );
  const current = data?.workspace === workspace ? data : undefined;
  return (
    <Flex
      vertical
      gap="large"
      style={{
        padding: theme.paddingLG,
        maxWidth: 1280,
        width: '100%',
        margin: '0 auto',
      }}
    >
      <Flex align="center" justify="space-between" wrap gap="middle">
        <Typography.Title level={2}>{t('Operations')}</Typography.Title>
        <Flex gap="small" wrap>
          <Button
            buttonStyle={projectsView ? 'tertiary' : 'primary'}
            onClick={() =>
              history.push(`${RoutePaths.OPERATIONS_BRIEF}${location.search}`)
            }
          >
            {t('Daily Brief')}
          </Button>
          <Button
            buttonStyle={projectsView ? 'primary' : 'tertiary'}
            onClick={() =>
              history.push(
                `${RoutePaths.OPERATIONS_PROJECTS}${location.search}`,
              )
            }
          >
            {t('Projects')}
          </Button>
          {current && workspace && settings?.capabilities.chat && (
            <OperationsChat
              key={`${workspace}:${location.pathname}`}
              workspace={workspace}
              view={projectsView ? 'projects' : 'daily_brief'}
              onChanged={refresh}
            />
          )}
          <Button
            onClick={refresh}
            disabled={loading || (!workspace && !error)}
          >
            {t('Refresh')}
          </Button>
        </Flex>
      </Flex>
      {settings && settings.workspaces.length > 1 && (
        <Select
          aria-label={t('Workspace')}
          value={workspace}
          disabled={Boolean(dialog) || Boolean(pendingTask)}
          options={settings.workspaces.map(item => ({
            value: item.id,
            label: item.name,
          }))}
          onChange={(value: string) =>
            history.replace(
              `${location.pathname}?workspace=${encodeURIComponent(value)}`,
            )
          }
        />
      )}
      {error && (
        <div role="alert">
          <Typography.Text type="danger">{error}</Typography.Text>
        </div>
      )}
      {!settings && !error && <Loading />}
      {settings && !workspace && (
        <Typography.Paragraph>
          {t('No authorized workspace is available.')}
        </Typography.Paragraph>
      )}
      {loading && !current && <Loading />}
      {current && settings && workspace && (
        <>
          {loading && <output>{t('Refreshing…')}</output>}
          {!projectsView ? (
            <>
              <Flex justify="space-between" align="center" wrap gap="small">
                <Typography.Text type="secondary">
                  {current.brief.day} · {current.brief.timezone}
                </Typography.Text>
                {settings.capabilities.create && (
                  <Button
                    disabled={loading}
                    onClick={() => setDialog({ resource: 'tasks' })}
                  >
                    {t('Add task')}
                  </Button>
                )}
              </Flex>
              <Row gutter={[16, 16]}>
                {[
                  {
                    title: t('Items for today'),
                    value: String(current.brief.items_today),
                    help: t(
                      'Tasks due on this business day, including completed tasks.',
                    ),
                  },
                  {
                    title: t('Total labor hours'),
                    value: current.brief.total_labor_hours.toLocaleString(
                      undefined,
                      {
                        maximumFractionDigits: 2,
                      },
                    ),
                    help: t(
                      'Total logged hours across all dates, excluding removed entries and removed projects.',
                    ),
                  },
                  {
                    title: t('Open jobs'),
                    value: String(current.brief.open_jobs),
                    help: t(
                      'Active projects excluding completed and cancelled statuses.',
                    ),
                  },
                ].map(item => (
                  <Col key={item.title} xs={24} sm={8}>
                    <Card size="small">
                      <Flex justify="space-between" align="center">
                        <Typography.Text>{item.title}</Typography.Text>
                        <Tooltip title={item.help}>
                          <Button
                            size="small"
                            buttonStyle="tertiary"
                            aria-label={t('About %s', item.title)}
                          >
                            i
                          </Button>
                        </Tooltip>
                      </Flex>
                      <Typography.Title level={3}>
                        {item.value}
                      </Typography.Title>
                    </Card>
                  </Col>
                ))}
              </Row>
              <SummaryUpdates
                key={workspace}
                workspace={workspace}
                day={current.brief.day}
                capabilities={settings.capabilities}
                externalRefreshToken={refreshToken}
              />
              <Card title={t('Today’s commitments')}>
                <Progress
                  percent={
                    current.brief.items_today
                      ? Math.round(
                          (current.brief.completed_today /
                            current.brief.items_today) *
                            100,
                        )
                      : 0
                  }
                />
                <Typography.Paragraph>
                  {t(
                    '%s of %s completed',
                    current.brief.completed_today,
                    current.brief.items_today,
                  )}
                </Typography.Paragraph>
                <Flex vertical gap="middle">
                  {current.brief.tasks.length === 0 && (
                    <Typography.Text type="secondary">
                      {t('No tasks due today')}
                    </Typography.Text>
                  )}
                  {current.brief.tasks.map(task => (
                    <Flex
                      data-operations-resource="tasks"
                      data-operations-record={task.id}
                      key={task.id}
                      justify="space-between"
                      wrap
                      gap="small"
                    >
                      <Flex vertical>
                        <Checkbox
                          checked={task.completed}
                          disabled={
                            !settings.capabilities.complete ||
                            Boolean(pendingTask) ||
                            loading
                          }
                          onChange={() => complete(task)}
                        >
                          {task.title}
                        </Checkbox>
                        <Typography.Text type="secondary">
                          {[
                            current.projects.find(
                              project => project.id === task.project_id,
                            )?.title,
                            task.assignee_name,
                            task.due_at &&
                              new Date(task.due_at).toLocaleTimeString(),
                          ]
                            .filter(Boolean)
                            .join(' · ')}
                        </Typography.Text>
                      </Flex>
                      <RecordActions
                        resource="tasks"
                        record={task}
                        workspace={workspace}
                        capabilities={settings.capabilities}
                        onEdit={setDialog}
                        onChanged={refresh}
                      />
                    </Flex>
                  ))}
                  {current.brief.tasks_has_more && (
                    <Button
                      onClick={() =>
                        history.push(
                          `${RoutePaths.OPERATIONS_PROJECTS}${location.search}`,
                        )
                      }
                    >
                      {t('View all tasks in Projects')}
                    </Button>
                  )}
                </Flex>
              </Card>
            </>
          ) : (
            <>
              <Flex justify="space-between" align="center" wrap gap="small">
                <Flex gap="small" align="center">
                  <Switch
                    aria-label={t('Include removed projects')}
                    checked={removed}
                    onChange={setRemoved}
                  />
                  <Typography.Text>
                    {t('Include removed projects')}
                  </Typography.Text>
                </Flex>
                {settings.capabilities.create && (
                  <Button
                    disabled={loading}
                    onClick={() => setDialog({ resource: 'projects' })}
                  >
                    {t('Add project')}
                  </Button>
                )}
              </Flex>
              <Collapse
                items={[
                  {
                    key: 'workers',
                    label: t('Worker directory'),
                    children: (
                      <Flex vertical gap="middle">
                        <Typography.Text type="secondary">
                          {t(
                            'Manage workers across projects. Removing a worker preserves their assignments and logged hours.',
                          )}
                        </Typography.Text>
                        <Flex
                          justify="space-between"
                          align="center"
                          wrap
                          gap="small"
                        >
                          <Flex gap="small" align="center">
                            <Switch
                              aria-label={t('Include removed workers')}
                              checked={removedWorkers}
                              onChange={setRemovedWorkers}
                            />
                            <Typography.Text>
                              {t('Include removed workers')}
                            </Typography.Text>
                          </Flex>
                          {settings.capabilities.create && (
                            <Button
                              disabled={loading}
                              onClick={() => setDialog({ resource: 'workers' })}
                            >
                              {t('Add worker')}
                            </Button>
                          )}
                        </Flex>
                        {current.workers.filter(
                          worker => removedWorkers || !worker.archived_at,
                        ).length === 0 && (
                          <Typography.Text type="secondary">
                            {t('No workers')}
                          </Typography.Text>
                        )}
                        {current.workers
                          .filter(
                            worker => removedWorkers || !worker.archived_at,
                          )
                          .map(worker => (
                            <Flex
                              key={worker.id}
                              justify="space-between"
                              align="center"
                              wrap
                              gap="small"
                              data-operations-resource="workers"
                              data-operations-record={worker.id}
                            >
                              <Typography.Text>
                                {worker.name}
                                {worker.archived_at ? ` · ${t('Removed')}` : ''}
                              </Typography.Text>
                              <RecordActions
                                resource="workers"
                                record={worker}
                                workspace={workspace}
                                capabilities={settings.capabilities}
                                onEdit={setDialog}
                                onChanged={refresh}
                              />
                            </Flex>
                          ))}
                      </Flex>
                    ),
                  },
                ]}
              />
              {current.projects.length === 0 && (
                <Card>
                  <Typography.Text type="secondary">
                    {t('No projects')}
                  </Typography.Text>
                </Card>
              )}
              <Collapse
                items={(Object.keys(statusLabels) as ProjectStatus[])
                  .filter(status =>
                    current.projects.some(project => project.status === status),
                  )
                  .map(status => {
                    const projects = current.projects.filter(
                      project => project.status === status,
                    );
                    return {
                      key: status,
                      label: `${statusLabels[status]} (${projects.length})`,
                      children: (
                        <Collapse
                          ghost
                          items={projects.map(project => {
                            const workerNames = current.assignments
                              .filter(item => item.project_id === project.id)
                              .map(
                                item =>
                                  current.workers.find(
                                    worker => worker.id === item.worker_id,
                                  )?.name || item.worker_id,
                              );
                            return {
                              key: project.id,
                              label: (
                                <Flex vertical gap="small">
                                  <Flex
                                    data-operations-resource="projects"
                                    data-operations-record={project.id}
                                    justify="space-between"
                                    wrap
                                    gap="small"
                                  >
                                    <Typography.Text strong>
                                      {project.title}
                                      {project.archived_at
                                        ? ` · ${t('Removed')}`
                                        : ''}
                                    </Typography.Text>
                                    <Typography.Text type="secondary">
                                      {new Date(
                                        project.updated_at,
                                      ).toLocaleString()}
                                    </Typography.Text>
                                  </Flex>
                                  {workerNames.length > 0 && (
                                    <Typography.Text type="secondary">
                                      {workerNames.join(', ')}
                                    </Typography.Text>
                                  )}
                                </Flex>
                              ),
                              children: (
                                <ProjectDetails
                                  project={project}
                                  workspace={workspace}
                                  workers={current.workers}
                                  capabilities={settings.capabilities}
                                  refreshToken={refreshToken}
                                  onEdit={setDialog}
                                  onChanged={refresh}
                                />
                              ),
                            };
                          })}
                        />
                      ),
                    };
                  })}
              />
            </>
          )}
          {dialog && (
            <RecordForm
              key={`${workspace}:${dialog.resource}:${dialog.record?.id || 'new'}`}
              dialog={dialog}
              workspace={workspace}
              projects={current.projects}
              workers={current.workers}
              day={current.brief.day}
              onClose={() => setDialog(undefined)}
              onSaved={() => {
                setDialog(undefined);
                refresh();
              }}
            />
          )}
        </>
      )}
    </Flex>
  );
}
