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
import { describeError, listRecords } from './api';
import { Capabilities, DialogState, SummaryUpdate } from './types';
import RecordForm from './RecordForm';
import RecordActions from './RecordActions';
import Attachments from './Attachments';

interface Props {
  workspace: string;
  day: string;
  capabilities: Capabilities;
  externalRefreshToken?: number;
}
/** Show every workspace summary for the selected date, regardless of source. */
export default function SummaryUpdates({
  workspace,
  day,
  capabilities,
  externalRefreshToken = 0,
}: Props) {
  const [selectedDay, setSelectedDay] = useState(day);
  const [byDate, setByDate] = useState(false);
  const [offset, setOffset] = useState(0);
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
    listRecords(
      workspace,
      'summaries',
      {
        summaryDate: byDate ? selectedDay : undefined,
        archived: removed,
        offset,
        limit: 20,
      },
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
  }, [
    workspace,
    selectedDay,
    removed,
    refreshToken,
    byDate,
    offset,
    externalRefreshToken,
  ]);
  return (
    <Card title={t('Summary updates')}>
      <Flex
        vertical
        gap="middle"
        data-summary-mode={byDate ? 'date' : 'all'}
        data-summary-date={byDate ? selectedDay : ''}
        data-summary-offset={offset}
        data-summary-removed={removed}
      >
        <Flex wrap gap="small" align="center">
          <Button
            disabled={Boolean(dialog)}
            onClick={() => {
              setByDate(false);
              setOffset(0);
            }}
            buttonStyle={!byDate ? 'primary' : 'tertiary'}
          >
            {t('All updates')}
          </Button>
          <Button
            disabled={Boolean(dialog)}
            onClick={() => {
              setByDate(true);
              setOffset(0);
            }}
            buttonStyle={byDate ? 'primary' : 'tertiary'}
          >
            {t('By date')}
          </Button>
          {byDate && (
            <>
              <label htmlFor="operations-summary-day">
                {t('View all updates for date')}
              </label>
              <Input
                id="operations-summary-day"
                type="date"
                value={selectedDay}
                disabled={Boolean(dialog)}
                onChange={event => {
                  if (event.target.value) {
                    setSelectedDay(event.target.value);
                    setOffset(0);
                  }
                }}
              />
            </>
          )}
          <Switch
            aria-label={t('Include removed summaries')}
            checked={removed}
            disabled={Boolean(dialog)}
            onChange={value => {
              setRemoved(value);
              setOffset(0);
            }}
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
            {byDate ? t('No summaries for this date') : t('No summaries')}
          </Typography.Text>
        )}
        {updates.map(update => (
          <Card
            data-operations-resource="summaries"
            data-operations-record={update.id}
            key={update.id}
            size="small"
            title={update.title}
          >
            <Typography.Text type="secondary">
              {update.summary_date} ·{' '}
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
            <Attachments
              workspace={workspace}
              summaryId={update.id}
              archived={Boolean(update.archived_at)}
              capabilities={capabilities}
              refreshToken={refreshToken}
              onChanged={refresh}
            />
          </Card>
        ))}
        <Flex gap="small">
          <Button
            disabled={loading || offset === 0}
            onClick={() => setOffset(value => Math.max(0, value - 20))}
          >
            {t('Previous')}
          </Button>
          <Typography.Text>
            {t('Page %s', String(offset / 20 + 1))}
          </Typography.Text>
          <Button
            disabled={loading || updates.length < 20}
            onClick={() => setOffset(value => value + 20)}
          >
            {t('Next')}
          </Button>
        </Flex>
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
