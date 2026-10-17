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
import { v4 as uuid } from 'uuid';
import { SupersetClient } from '@superset-ui/core';
import { t } from '@apache-superset/core/translation';
import {
  Button,
  Drawer,
  Flex,
  Input,
  Typography,
} from '@superset-ui/core/components';
import { describeError } from './api';

interface Proposal {
  id: string;
  changes: string[];
}
interface Reply {
  reply: string;
  proposal?: Proposal | null;
}
interface Message {
  id: string;
  text: string;
  proposal?: Proposal | null;
  resolved?: boolean;
}
/** Capture visible references, not an authoritative copy of business data. */
export function visibleOperationsContext(view: string) {
  const elements = Array.from(
    document.querySelectorAll<HTMLElement>('[data-operations-record]'),
  ).filter(element => element.getClientRects().length > 0);
  const records = Array.from(
    new Map(
      elements.map(element => [
        `${element.dataset.operationsResource}:${element.dataset.operationsRecord}`,
        {
          resource: element.dataset.operationsResource,
          id: element.dataset.operationsRecord,
        },
      ]),
    ).values(),
  );
  const summaries = document.querySelector<HTMLElement>('[data-summary-mode]');
  return {
    view,
    records,
    summary_mode: summaries?.dataset.summaryMode,
    summary_date: summaries?.dataset.summaryDate,
    offset: summaries?.dataset.summaryOffset,
    include_removed: summaries?.dataset.summaryRemoved,
  };
}
/** Reuse the dashboard's nonblocking drawer and explicit retry behavior. */
export default function OperationsChat({
  workspace,
  view,
  onChanged,
}: {
  workspace: string;
  view: string;
  onChanged: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState('');
  const [messages, setMessages] = useState<Message[]>([]);
  const [conversation, setConversation] = useState(() => uuid());
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
  const active = useRef<AbortController>();
  useEffect(() => () => active.current?.abort(), []);
  const request = async (path: string, payload: Record<string, unknown>) => {
    const controller = new AbortController();
    active.current = controller;
    setPending(true);
    setError('');
    const timer = window.setTimeout(() => controller.abort(), 100000);
    try {
      const response = await SupersetClient.post({
        endpoint: `/api/v1/operations/workspaces/${encodeURIComponent(workspace)}/chat${path}`,
        jsonPayload: { conversation_id: conversation, ...payload },
        signal: controller.signal,
        fetchRetryOptions: { retries: 0, retryOn: [] },
      });
      if (controller.signal.aborted)
        throw new Error(
          t('Request cancelled. Check saved records before retrying a change.'),
        );
      return response.json.result;
    } finally {
      window.clearTimeout(timer);
      if (active.current === controller) {
        active.current = undefined;
        setPending(false);
      }
    }
  };
  const send = async () => {
    if (active.current || !draft.trim()) return;
    const question = draft;
    try {
      const result = (await request('', {
        message: question,
        context: visibleOperationsContext(view),
      })) as Reply;
      setMessages(items => [
        ...items,
        { id: uuid(), text: question },
        { id: uuid(), text: result.reply, proposal: result.proposal },
      ]);
      setDraft('');
    } catch (failure) {
      setError((await describeError(failure)).message);
    }
  };
  const decide = async (message: Message, decision: 'apply' | 'cancel') => {
    if (active.current || !message.proposal) return;
    try {
      await request(`/proposals/${message.proposal.id}`, { decision });
      setMessages(items =>
        items.map(item =>
          item.id === message.id
            ? {
                ...item,
                resolved: true,
                text: `${item.text}\n${decision === 'apply' ? t('Changes applied.') : t('Proposal cancelled.')}`,
              }
            : item,
        ),
      );
      if (decision === 'apply') onChanged();
    } catch (failure) {
      setError((await describeError(failure)).message);
    }
  };
  return (
    <>
      <Button onClick={() => setOpen(true)}>{t('Ask Jason')}</Button>
      <Drawer
        title={t('Ask Jason · Operations')}
        open={open}
        onClose={() => setOpen(false)}
        size={440}
        mask={false}
        extra={
          <Button
            disabled={pending}
            onClick={() => {
              setConversation(uuid());
              setMessages([]);
              setDraft('');
              setError('');
            }}
          >
            {t('New conversation')}
          </Button>
        }
        footer={
          <Flex vertical gap="small">
            {error && (
              <Typography.Text role="alert" type="danger">
                {error}
              </Typography.Text>
            )}
            <Input.TextArea
              aria-label={t('Message Jason')}
              value={draft}
              onChange={event => setDraft(event.target.value)}
              disabled={pending}
              maxLength={8000}
            />
            <Button
              disabled={pending || !draft.trim()}
              loading={pending}
              onClick={send}
            >
              {t('Send')}
            </Button>
          </Flex>
        }
      >
        <Flex vertical gap="middle">
          {messages.map(message => (
            <Flex key={message.id} vertical gap="small">
              <Typography.Paragraph style={{ whiteSpace: 'pre-wrap' }}>
                {message.text}
              </Typography.Paragraph>
              {message.proposal && (
                <>
                  <Typography.Text strong>
                    {t('Proposed changes — review before applying')}
                  </Typography.Text>
                  <ul>
                    {message.proposal.changes.map((change, index) => (
                      <li key={index}>{change}</li>
                    ))}
                  </ul>
                  {!message.resolved && (
                    <Flex gap="small">
                      <Button
                        disabled={pending}
                        onClick={() => decide(message, 'apply')}
                      >
                        {t('Apply')}
                      </Button>
                      <Button
                        disabled={pending}
                        onClick={() => decide(message, 'cancel')}
                      >
                        {t('Cancel')}
                      </Button>
                    </Flex>
                  )}
                </>
              )}
            </Flex>
          ))}
        </Flex>
      </Drawer>
    </>
  );
}
