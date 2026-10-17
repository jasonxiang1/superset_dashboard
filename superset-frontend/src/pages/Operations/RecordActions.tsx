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

import { useState } from 'react';
import { t } from '@apache-superset/core/translation';
import { Button, Flex, Modal, Typography } from '@superset-ui/core/components';
import {
  Capabilities,
  DialogState,
  HistoryEntry,
  WritableRecord,
  WritableResource,
} from './types';
import { changeRecord, describeError, getHistory } from './api';

interface Props {
  resource: WritableResource;
  record: WritableRecord;
  workspace: string;
  capabilities: Capabilities;
  onEdit: (dialog: DialogState) => void;
  onChanged: () => void;
}
/** Offer only authorized actions and confirm recoverable removal. */
export default function RecordActions({
  resource,
  record,
  workspace,
  capabilities,
  onEdit,
  onChanged,
}: Props) {
  const [confirm, setConfirm] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
  const [history, setHistory] = useState<HistoryEntry[] | null>(null);
  const archived = Boolean(record.archived_at);
  const label =
    'title' in record
      ? record.title
      : 'name' in record
        ? record.name
        : 'body' in record
          ? record.body.slice(0, 60)
          : 'work_date' in record
            ? t('Labor entry: %s', record.work_date)
            : t('Worker assignment');
  const mutate = async () => {
    setPending(true);
    try {
      await changeRecord(
        workspace,
        resource,
        record,
        archived ? 'restore' : 'archive',
      );
      setConfirm(false);
      setError('');
      onChanged();
    } catch (failure) {
      setError((await describeError(failure)).message);
    } finally {
      setPending(false);
    }
  };
  const loadHistory = async (more = false) => {
    setPending(true);
    try {
      const entries = await getHistory(
        workspace,
        resource,
        record.id,
        more ? history?.length : 0,
      );
      setHistory(more ? [...(history || []), ...entries] : entries);
      setError('');
    } catch (failure) {
      setError((await describeError(failure)).message);
    } finally {
      setPending(false);
    }
  };
  return (
    <>
      <Flex gap="small" wrap>
        {!archived && resource !== 'assignments' && capabilities.edit && (
          <Button
            size="small"
            buttonStyle="tertiary"
            onClick={() => onEdit({ resource, record })}
          >
            {t('Edit')}
          </Button>
        )}
        {(archived ? capabilities.restore : capabilities.remove) && (
          <Button
            size="small"
            buttonStyle="tertiary"
            disabled={pending}
            onClick={() => {
              setError('');
              setConfirm(true);
            }}
          >
            {archived ? t('Restore') : t('Remove')}
          </Button>
        )}
        {capabilities.edit && (
          <Button
            size="small"
            buttonStyle="tertiary"
            disabled={pending}
            onClick={() => loadHistory()}
          >
            {t('History')}
          </Button>
        )}
      </Flex>
      {error && !confirm && (
        <Typography.Text role="alert" type="danger">
          {error}
        </Typography.Text>
      )}
      {confirm && (
        <Modal
          show
          title={archived ? t('Restore record') : t('Remove record')}
          onHide={() => {
            if (!pending) setConfirm(false);
          }}
          primaryButtonName={archived ? t('Restore') : t('Remove')}
          onHandledPrimaryAction={() => mutate()}
          primaryButtonLoading={pending}
          disablePrimaryButton={pending}
          closable={!pending}
          maskClosable={false}
        >
          <Typography.Paragraph>
            {archived
              ? t('Restore “%s”?', label)
              : t(
                  'Remove “%s” from active records? It can be restored.',
                  label,
                )}
          </Typography.Paragraph>
          {resource === 'projects' && !archived && (
            <Typography.Paragraph>
              {t(
                'Linked records and financial history will be preserved. Restore this project before changing its linked records.',
              )}
            </Typography.Paragraph>
          )}
          {error && (
            <>
              <Typography.Text role="alert" type="danger">
                {error}
              </Typography.Text>
              <Button
                onClick={() => {
                  setConfirm(false);
                  onChanged();
                }}
              >
                {t('Reload records')}
              </Button>
            </>
          )}
        </Modal>
      )}
      {history && (
        <Modal
          show
          responsive
          title={t('Record history')}
          onHide={() => setHistory(null)}
          hideFooter
        >
          <Flex vertical gap="middle">
            {history.map(entry => (
              <div key={entry.id}>
                <Typography.Text strong>
                  {entry.action} ·{' '}
                  {new Date(entry.occurred_at).toLocaleString()}
                </Typography.Text>
                <Typography.Paragraph>
                  {t('User %s', entry.actor_id)}
                </Typography.Paragraph>
                <pre
                  style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}
                >
                  {JSON.stringify(
                    { before: entry.before, after: entry.after },
                    null,
                    2,
                  )}
                </pre>
              </div>
            ))}
          </Flex>
          {history.length > 0 && history.length % 50 === 0 && (
            <Button disabled={pending} onClick={() => loadHistory(true)}>
              {t('Load older changes')}
            </Button>
          )}
        </Modal>
      )}
    </>
  );
}
