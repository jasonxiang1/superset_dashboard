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
import { useEffect, useRef, useState } from 'react';
import { useSelector } from 'react-redux';
import { v4 as uuidv4 } from 'uuid';
import { SupersetClient } from '@superset-ui/core';
import { t } from '@apache-superset/core/translation';
import {
  Button,
  Drawer,
  Flex,
  Input,
  Select,
  Typography,
} from '@superset-ui/core/components';
import { RootState } from 'src/dashboard/types';

type Props = { dashboardId: number; dashboardTitle: string };
type Message = { id: string; role: 'user' | 'assistant'; text: string };
type ContextState = Pick<
  RootState,
  | 'sliceEntities'
  | 'datasources'
  | 'dataMask'
  | 'nativeFilters'
  | 'dashboardFilters'
>;

/** Collect definitions and filter state without sending chart rows or credentials. */
export function getDashboardContext(
  state: ContextState,
  dashboard: Props,
  selectedChart?: number,
) {
  const slices = Object.values(state.sliceEntities.slices);
  const datasetIds = new Set(slices.map(slice => slice.form_data.datasource));
  return {
    dashboard,
    selectedChart,
    charts: slices.map(slice => ({
      id: slice.slice_id,
      name: slice.slice_name,
      description: slice.description,
      visualization: slice.viz_type,
      configuration: slice.form_data as Record<string, unknown>,
      dataset: slice.form_data.datasource,
    })),
    datasets: Object.entries(state.datasources)
      .filter(([id]) => datasetIds.has(id))
      .map(([id, dataset]) => ({
        id,
        name: dataset.table_name,
        metrics: dataset.metrics?.map(metric => ({
          name: metric.metric_name,
          expression: metric.expression,
          description: metric.description,
        })),
        columns: dataset.columns?.map(column => ({
          name: column.column_name,
          expression: column.expression,
          description: column.description,
        })),
      })),
    filterDefinitions: state.nativeFilters.filters,
    filterState: Object.fromEntries(
      Object.entries(state.dataMask).map(([id, mask]) => [
        id,
        { extraFormData: mask.extraFormData, filterState: mask.filterState },
      ]),
    ),
    legacyFilters: state.dashboardFilters,
  };
}

/** Chat with the configured agent while keeping history for this mounted dashboard. */
export default function OpenClawChat({ dashboardId, dashboardTitle }: Props) {
  const sliceEntities = useSelector((state: RootState) => state.sliceEntities);
  const datasources = useSelector((state: RootState) => state.datasources);
  const dataMask = useSelector((state: RootState) => state.dataMask);
  const nativeFilters = useSelector((state: RootState) => state.nativeFilters);
  const dashboardFilters = useSelector(
    (state: RootState) => state.dashboardFilters,
  );
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState('');
  const [messages, setMessages] = useState<Message[]>([]);
  const [selectedChart, setSelectedChart] = useState<number>();
  const [conversationId, setConversationId] = useState(() => uuidv4());
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
  const requestRef = useRef<AbortController>();
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => () => requestRef.current?.abort(), []);
  useEffect(() => {
    if (open) endRef.current?.scrollIntoView?.({ block: 'nearest' });
  }, [messages, pending, open]);

  const send = async () => {
    const question = draft.trim();
    if (!question || requestRef.current) return;
    setError('');
    const payload = {
      message: question,
      conversation_id: conversationId,
      context: getDashboardContext(
        {
          sliceEntities,
          datasources,
          dataMask,
          nativeFilters,
          dashboardFilters,
        },
        { dashboardId, dashboardTitle },
        selectedChart,
      ),
    };
    if (new Blob([JSON.stringify(payload)]).size > 256 * 1024) {
      setError(t('This dashboard has too much context to send to OpenClaw.'));
      return;
    }
    const controller = new AbortController();
    requestRef.current = controller;
    setPending(true);
    try {
      const { json } = await SupersetClient.post({
        endpoint: '/api/v1/openclaw/chat',
        jsonPayload: payload,
        signal: controller.signal,
        timeout: 100000,
        fetchRetryOptions: { retries: 0, retryOn: [] },
      });
      if (controller.signal.aborted) return;
      if (typeof json.result !== 'string' || !json.result.trim()) {
        throw new Error('Empty response');
      }
      setMessages(previous => [
        ...previous,
        { id: uuidv4(), role: 'user', text: question },
        { id: uuidv4(), role: 'assistant', text: json.result },
      ]);
      setDraft('');
    } catch {
      if (!controller.signal.aborted) {
        setError(
          t(
            'Cannot connect to OpenClaw right now. Please check that the agent is running and the connection is configured, then try again.',
          ),
        );
      }
    } finally {
      if (!controller.signal.aborted) setPending(false);
      requestRef.current = undefined;
    }
  };

  return (
    <>
      <Button buttonStyle="secondary" onClick={() => setOpen(true)}>
        {t('Ask Jason')}
      </Button>
      <Drawer
        title={t('Ask Jason')}
        open={open}
        onClose={() => setOpen(false)}
        size={440}
        mask={false}
        extra={
          <Button
            disabled={pending}
            onClick={() => {
              setConversationId(uuidv4());
              setMessages([]);
              setError('');
              setDraft('');
            }}
          >
            {t('New conversation')}
          </Button>
        }
        footer={
          <Flex vertical gap="small">
            {error && <Typography.Text role="alert">{error}</Typography.Text>}
            <Input.TextArea
              aria-label={t('Message to Jason')}
              placeholder={t('Ask about this dashboard…')}
              value={draft}
              onChange={event => setDraft(event.target.value)}
              disabled={pending}
              maxLength={8000}
              autoSize={{ minRows: 2, maxRows: 6 }}
            />
            <Button
              buttonStyle="primary"
              disabled={!draft.trim() || pending}
              loading={pending}
              onClick={send}
            >
              {pending ? t('Waiting for Jason…') : t('Send')}
            </Button>
          </Flex>
        }
      >
        <Flex vertical gap="middle">
          <Typography.Text strong>{dashboardTitle}</Typography.Text>
          <Select
            ariaLabel={t('Chart to discuss')}
            placeholder={t('Whole dashboard')}
            allowClear
            value={selectedChart}
            onChange={(value: number | undefined) => setSelectedChart(value)}
            options={Object.values(sliceEntities.slices).map(slice => ({
              value: slice.slice_id,
              label: slice.slice_name,
            }))}
            disabled={pending}
          />
          <Typography.Text type="secondary">
            {t(
              'Dashboard definitions and active filters are included with each message.',
            )}
          </Typography.Text>
          <div
            role="log"
            aria-label={t('Conversation with Jason')}
            aria-live="polite"
          >
            {messages.map(message => (
              <div key={message.id}>
                <Typography.Text strong>
                  {message.role === 'user' ? t('You') : t('Jason')}
                </Typography.Text>
                <Typography.Paragraph style={{ whiteSpace: 'pre-wrap' }}>
                  {message.text}
                </Typography.Paragraph>
              </div>
            ))}
            {pending && <output>{t('Waiting for Jason…')}</output>}
            <div ref={endRef} />
          </div>
        </Flex>
      </Drawer>
    </>
  );
}
