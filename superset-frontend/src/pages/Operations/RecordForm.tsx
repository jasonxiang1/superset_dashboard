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

import { useRef, useState } from 'react';
import { v4 as uuid } from 'uuid';
import { t } from '@apache-superset/core/translation';
import {
  Button,
  Form,
  FormItem,
  Modal,
  Select,
  Typography,
} from '@superset-ui/core/components';
import { Input, InputNumber } from '@superset-ui/core/components/Input';
import { describeError, getRecord, saveRecord } from './api';
import { DialogState, Project, Worker, WritableRecord } from './types';

export const statusLabels = {
  planned: t('Planned'),
  in_progress: t('In progress'),
  on_hold: t('On hold'),
  completed: t('Completed'),
  cancelled: t('Cancelled'),
};
export const resourceLabels = {
  projects: t('Project'),
  tasks: t('Task'),
  labor_entries: t('Labor entry'),
  notes: t('Project note'),
  assignments: t('Worker assignment'),
};
interface Values {
  title?: string;
  description?: string;
  status?: string;
  project_id?: string;
  assignee_name?: string | null;
  due_at?: string | null;
  worker_id?: string;
  work_date?: string;
  minutes?: number;
  note?: string;
  body?: string;
}
function localDateTime(iso: string | null): string {
  if (!iso) return '';
  const date = new Date(iso);
  return new Date(date.getTime() - date.getTimezoneOffset() * 60000)
    .toISOString()
    .slice(0, 16);
}
function formValues(record?: WritableRecord): Values {
  if (!record) return {};
  return {
    ...('title' in record
      ? { title: record.title, description: record.description }
      : {}),
    ...('status' in record ? { status: record.status } : {}),
    ...('due_at' in record
      ? {
          due_at: localDateTime(record.due_at),
          assignee_name: record.assignee_name,
        }
      : {}),
    ...('worker_id' in record ? { worker_id: record.worker_id } : {}),
    ...('work_date' in record
      ? {
          work_date: record.work_date,
          minutes: record.minutes,
          note: record.note,
        }
      : {}),
    ...('body' in record ? { body: record.body } : {}),
  };
}
interface Props {
  dialog: DialogState;
  workspace: string;
  projects: Project[];
  workers: Worker[];
  day: string;
  onClose: () => void;
  onSaved: () => void;
}
/** Edit a business record while retaining drafts and revision conflicts. */
export default function RecordForm({
  dialog,
  workspace,
  projects,
  workers,
  day,
  onClose,
  onSaved,
}: Props) {
  const [form] = Form.useForm<Values>();
  const [record, setRecord] = useState(dialog.record);
  const [saving, setSaving] = useState(false);
  const [failure, setFailure] = useState<{
    message: string;
    status?: number;
  }>();
  const requestId = useRef(uuid());
  const submitted = useRef<Record<string, unknown>>();
  const [retryOnly, setRetryOnly] = useState(false);
  const { resource, projectId } = dialog;
  const isProject = resource === 'projects';
  const isTask = resource === 'tasks';
  const isLabor = resource === 'labor_entries';
  const isAssignment = resource === 'assignments';
  const required = [{ required: true, message: t('This field is required.') }];

  const submit = async (values: Values) => {
    setSaving(true);
    setFailure(undefined);
    let payload: Record<string, unknown> = { ...values };
    if (isTask && 'due_at' in payload)
      payload.due_at = values.due_at
        ? new Date(values.due_at).toISOString()
        : null;
    if (record) payload = { ...payload, revision: record.revision };
    else
      payload = {
        ...payload,
        request_id: requestId.current,
        ...(!isProject && projectId ? { project_id: projectId } : {}),
      };
    if (retryOnly && submitted.current) payload = submitted.current;
    submitted.current = payload;
    try {
      await saveRecord(workspace, resource, payload, record?.id);
      onSaved();
    } catch (error) {
      const detail = await describeError(error);
      setFailure(detail);
      // Preserve the exact create request after an ambiguous transport failure.
      setRetryOnly(!record && (detail.status == null || detail.status >= 500));
    } finally {
      setSaving(false);
    }
  };
  const reload = async () => {
    if (!record) return;
    setSaving(true);
    try {
      const latest = await getRecord(workspace, resource, record.id);
      setRecord(latest);
      form.resetFields();
      form.setFieldsValue(formValues(latest));
      setFailure(undefined);
    } catch (error) {
      setFailure(await describeError(error));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal
      show
      responsive
      title={
        record
          ? t('Edit %s', resourceLabels[resource])
          : t('Add %s', resourceLabels[resource])
      }
      onHide={() => {
        if (!saving) onClose();
      }}
      closable={!saving}
      maskClosable={false}
      primaryButtonName={retryOnly ? t('Retry submission') : t('Save')}
      primaryButtonLoading={saving}
      disablePrimaryButton={saving}
      onHandledPrimaryAction={() => {
        if (retryOnly) submit({});
        else form.submit();
      }}
    >
      {failure && (
        <div role="alert">
          <Typography.Paragraph type="danger">
            {failure.message}
          </Typography.Paragraph>
          {failure.status === 409 && record && (
            <Button onClick={() => reload()} disabled={saving}>
              {t('Reload saved record')}
            </Button>
          )}
          {retryOnly && (
            <Typography.Paragraph>
              {t(
                'The save could not be confirmed. Retry the same submission before making changes.',
              )}
            </Typography.Paragraph>
          )}
        </div>
      )}
      <Form
        form={form}
        layout="vertical"
        disabled={saving || retryOnly}
        onFinish={submit}
        initialValues={{
          ...(isProject || isTask ? { description: '' } : {}),
          ...(isProject ? { status: 'planned' } : {}),
          ...(isLabor ? { note: '', work_date: day } : {}),
          ...formValues(record),
        }}
      >
        {!isProject && !record && !projectId && (
          <FormItem label={t('Project')} name="project_id" rules={required}>
            <Select
              aria-label={t('Project')}
              options={projects
                .filter(project => !project.archived_at)
                .map(project => ({ value: project.id, label: project.title }))}
            />
          </FormItem>
        )}
        {(isProject || isTask) && (
          <>
            <FormItem
              label={t('Title')}
              name="title"
              rules={[...required, { whitespace: true }, { max: 200 }]}
            >
              <Input maxLength={200} />
            </FormItem>
            <FormItem label={t('Description')} name="description">
              <Input.TextArea
                maxLength={8000}
                autoSize={{ minRows: 2, maxRows: 6 }}
              />
            </FormItem>
          </>
        )}
        {isProject && (
          <FormItem label={t('Status')} name="status" rules={required}>
            <Select
              aria-label={t('Status')}
              options={Object.entries(statusLabels).map(([value, label]) => ({
                value,
                label,
              }))}
            />
          </FormItem>
        )}
        {isTask && (
          <>
            <FormItem label={t('Assignee')} name="assignee_name">
              <Input maxLength={200} />
            </FormItem>
            <FormItem label={t('Due time (device timezone)')} name="due_at">
              <Input type="datetime-local" />
            </FormItem>
          </>
        )}
        {(isLabor || isAssignment) && (
          <FormItem label={t('Worker')} name="worker_id" rules={required}>
            <Select
              aria-label={t('Worker')}
              options={workers
                .filter(
                  worker =>
                    !worker.archived_at ||
                    (record &&
                      'worker_id' in record &&
                      record.worker_id === worker.id),
                )
                .map(worker => ({ value: worker.id, label: worker.name }))}
            />
          </FormItem>
        )}
        {isLabor && (
          <>
            <FormItem label={t('Work date')} name="work_date" rules={required}>
              <Input type="date" />
            </FormItem>
            <FormItem
              label={t('Labor minutes')}
              name="minutes"
              rules={[...required, { type: 'number', min: 1, max: 1440 }]}
            >
              <InputNumber min={1} max={1440} precision={0} />
            </FormItem>
            <FormItem label={t('Note')} name="note">
              <Input.TextArea maxLength={8000} />
            </FormItem>
          </>
        )}
        {resource === 'notes' && (
          <FormItem
            label={t('Note')}
            name="body"
            rules={[...required, { whitespace: true }]}
          >
            <Input.TextArea
              maxLength={8000}
              autoSize={{ minRows: 3, maxRows: 10 }}
            />
          </FormItem>
        )}
      </Form>
    </Modal>
  );
}
