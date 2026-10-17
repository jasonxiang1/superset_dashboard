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
import { Button, Flex, Typography } from '@superset-ui/core/components';
import { Attachment, Capabilities, DialogState } from './types';
import { describeError, listAll } from './api';
import RecordForm from './RecordForm';
import RecordActions from './RecordActions';

/** Only known document providers can embed frames; other links remain links. */
export function previewLink(
  value: string,
): { url: string; image: boolean } | undefined {
  try {
    const url = new URL(value);
    if (url.protocol !== 'https:' || url.username || url.password)
      return undefined;
    if (/\.(png|jpe?g|gif|webp)$/i.test(url.pathname))
      return { url: url.href, image: true };
    if (url.hostname === 'drive.google.com') {
      const id = url.pathname.match(/^\/file\/d\/([a-zA-Z0-9_-]+)/)?.[1];
      if (id)
        return {
          url: `https://drive.google.com/file/d/${id}/preview`,
          image: false,
        };
    }
    if (
      url.hostname === 'docs.google.com' &&
      /^\/(document|spreadsheets|presentation)\/d\/[a-zA-Z0-9_-]+/.test(
        url.pathname,
      )
    )
      return {
        url: `${url.origin}${url.pathname.split('/').slice(0, 4).join('/')}/preview`,
        image: false,
      };
    if (/\.pdf$/i.test(url.pathname)) return { url: url.href, image: false };
  } catch {
    /* Invalid imported links remain visible without navigation. */
  }
  return undefined;
}
function Preview({ attachment }: { attachment: Attachment }) {
  const [failed, setFailed] = useState(false);
  const preview = previewLink(attachment.url);
  return (
    <Flex vertical gap="small">
      {/^https?:\/\//i.test(attachment.url) ? (
        <a href={attachment.url} target="_blank" rel="noopener noreferrer">
          {attachment.name} · {t('Open original')}
        </a>
      ) : (
        <Typography.Text>{attachment.name}</Typography.Text>
      )}
      {!attachment.archived_at &&
        preview &&
        !failed &&
        (preview.image ? (
          <img
            src={preview.url}
            alt={attachment.name}
            loading="lazy"
            referrerPolicy="no-referrer"
            onError={() => setFailed(true)}
            style={{ maxWidth: '100%', maxHeight: 320, objectFit: 'contain' }}
          />
        ) : (
          <iframe
            title={attachment.name}
            src={preview.url}
            loading="lazy"
            sandbox={
              ['drive.google.com', 'docs.google.com'].includes(
                new URL(preview.url).hostname,
              )
                ? 'allow-scripts allow-same-origin'
                : ''
            }
            referrerPolicy="no-referrer"
            height={320}
            onError={() => setFailed(true)}
          />
        ))}
      <Typography.Text type="secondary">
        {failed || !preview
          ? t('⚠ Preview unavailable. Check the link or sharing permissions.')
          : t(
              'If the preview is blank or access is denied, open the original and check sharing permissions.',
            )}
      </Typography.Text>
    </Flex>
  );
}
interface Props {
  workspace: string;
  projectId?: string;
  summaryId?: string;
  archived: boolean;
  capabilities: Capabilities;
  refreshToken: number;
  onChanged: () => void;
}
/** Manage external references without uploading or copying file content. */
export default function Attachments({
  workspace,
  projectId,
  summaryId,
  archived,
  capabilities,
  refreshToken,
  onChanged,
}: Props) {
  const [items, setItems] = useState<Attachment[]>([]);
  const [removed, setRemoved] = useState(false);
  const [dialog, setDialog] = useState<DialogState>();
  const [error, setError] = useState('');
  useEffect(() => {
    const controller = new AbortController();
    listAll(
      workspace,
      'attachments',
      { projectId, summaryId, archived: removed || archived },
      controller.signal,
    )
      .then(result => {
        if (!controller.signal.aborted) {
          setItems(result);
          setError('');
        }
      })
      .catch(async failure => {
        const detail = await describeError(failure);
        if (!controller.signal.aborted) setError(detail.message);
      });
    return () => controller.abort();
  }, [workspace, projectId, summaryId, removed, archived, refreshToken]);
  return (
    <Flex vertical gap="small">
      <Typography.Title level={5}>{t('Photos and documents')}</Typography.Title>
      {error && (
        <Typography.Text role="alert" type="danger">
          {error}
        </Typography.Text>
      )}
      <Flex gap="small">
        {!archived && capabilities.create && (
          <Button
            onClick={() =>
              setDialog({ resource: 'attachments', projectId, summaryId })
            }
          >
            {t('Add photo or document link')}
          </Button>
        )}
        <Button onClick={() => setRemoved(value => !value)}>
          {removed ? t('Hide removed links') : t('Include removed links')}
        </Button>
      </Flex>
      {items.map(item => (
        <Flex
          data-operations-resource="attachments"
          data-operations-record={item.id}
          key={`${item.id}:${item.revision}`}
          vertical
          gap="small"
        >
          <Preview attachment={item} />
          {!archived && (
            <RecordActions
              workspace={workspace}
              resource="attachments"
              record={item}
              capabilities={capabilities}
              onEdit={setDialog}
              onChanged={onChanged}
            />
          )}
        </Flex>
      ))}
      {dialog && (
        <RecordForm
          dialog={dialog}
          workspace={workspace}
          projects={[]}
          workers={[]}
          day=""
          onClose={() => setDialog(undefined)}
          onSaved={() => {
            setDialog(undefined);
            onChanged();
          }}
        />
      )}
    </Flex>
  );
}
