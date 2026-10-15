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
  Card,
  Flex,
  Loading,
  Switch,
  Typography,
} from '@superset-ui/core/components';
import { Input } from '@superset-ui/core/components/Input';
import { describeError, listAll } from './api';
import { Capabilities, DialogState, SummaryUpdate } from './types';
import RecordForm from './RecordForm';
import RecordActions from './RecordActions';

interface Props {
  workspace: string;
  day: string;
  capabilities: Capabilities;
}
/** Show every workspace summary for the selected date, regardless of source. */
export default function SummaryUpdates({
  workspace,
  day,
  capabilities,
}: Props) {
  const [selectedDay, setSelectedDay] = useState(day);
  const [removed, setRemoved] = useState(false);
  const [updates, setUpdates] = useState<SummaryUpdate[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [dialog, setDialog] = useState<DialogState>();
  const [refreshToken, setRefreshToken] = useState(0);
  const refresh = () => setRefreshToken(value => value + 1);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError('');
    setUpdates([]);
    listAll(
      workspace,
      'summaries',
      { summaryDate: selectedDay, archived: removed },
      controller.signal,
    )
      .then(records => {
        if (!controller.signal.aborted) setUpdates(records);
      })
      .catch(async failure => {
        const detail = await describeError(failure);
        if (!controller.signal.aborted) setError(detail.message);
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [workspace, selectedDay, removed, refreshToken]);
  return (
    <Card title={t('Summary updates')}>
      <Flex vertical gap="middle">
        <Flex wrap gap="small" align="center">
          <label htmlFor="operations-summary-day">
            {t('View all updates for date')}
          </label>
          <Input
            id="operations-summary-day"
            type="date"
            value={selectedDay}
            disabled={Boolean(dialog)}
            onChange={event => {
              if (event.target.value) setSelectedDay(event.target.value);
            }}
          />
          <Switch
            aria-label={t('Include removed summaries')}
            checked={removed}
            disabled={Boolean(dialog)}
            onChange={setRemoved}
          />
          <Typography.Text>{t('Include removed summaries')}</Typography.Text>
          {capabilities.create && (
            <Button
              disabled={loading || Boolean(error)}
              onClick={() => setDialog({ resource: 'summaries' })}
            >
              {t('Add summary')}
            </Button>
          )}
        </Flex>
        {error && (
          <Typography.Paragraph role="alert" type="danger">
            {error}
          </Typography.Paragraph>
        )}
        {loading && <Loading />}
        {!loading && !error && updates.length === 0 && (
          <Typography.Text type="secondary">
            {t('No summaries for this date')}
          </Typography.Text>
        )}
        {updates.map(update => (
          <Card key={update.id} size="small" title={update.title}>
            <Typography.Text type="secondary">
              {update.source === 'manual'
                ? t('Manual entry')
                : t('PostgreSQL integration')}
              {update.archived_at ? ` · ${t('Removed')}` : ''}
            </Typography.Text>
            <Typography.Paragraph style={{ whiteSpace: 'pre-wrap' }}>
              {update.body}
            </Typography.Paragraph>
            <RecordActions
              resource="summaries"
              record={update}
              workspace={workspace}
              capabilities={capabilities}
              onEdit={setDialog}
              onChanged={refresh}
            />
          </Card>
        ))}
      </Flex>
      {dialog && (
        <RecordForm
          key={`${dialog.resource}-${dialog.record?.id || 'new'}`}
          dialog={dialog}
          workspace={workspace}
          projects={[]}
          workers={[]}
          day={selectedDay}
          onClose={() => setDialog(undefined)}
          onSaved={() => {
            setDialog(undefined);
            refresh();
          }}
        />
      )}
    </Card>
  );
}
