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

export type ProjectStatus =
  | 'planned'
  | 'in_progress'
  | 'on_hold'
  | 'completed'
  | 'cancelled';
export interface BaseRecord {
  id: string;
  workspace_id: string;
  revision: number;
  created_at: string;
  updated_at: string;
  archived_at: string | null;
}
export interface Project extends BaseRecord {
  title: string;
  description: string;
  status: ProjectStatus;
}
export interface Task extends BaseRecord {
  project_id: string;
  title: string;
  description: string;
  assignee_name: string | null;
  due_at: string | null;
  completed: boolean;
  completed_at: string | null;
}
export interface ProjectNote extends BaseRecord {
  project_id: string;
  body: string;
}
export interface SummaryUpdate extends BaseRecord {
  summary_date: string;
  title: string;
  body: string;
  source: 'manual' | 'postgresql';
}
export interface Assignment extends BaseRecord {
  project_id: string;
  worker_id: string;
}
export interface Worker extends BaseRecord {
  name: string;
}
export interface LaborEntry extends BaseRecord {
  project_id: string;
  worker_id: string;
  work_date: string;
  minutes: number;
  note: string;
}
export interface Attachment extends BaseRecord {
  project_id: string | null;
  summary_id: string | null;
  name: string;
  url: string;
}
export interface RecordMap {
  projects: Project;
  tasks: Task;
  notes: ProjectNote;
  assignments: Assignment;
  labor_entries: LaborEntry;
  workers: Worker;
  attachments: Attachment;
  summaries: SummaryUpdate;
}
export type Resource = keyof RecordMap;
export type WritableResource = Resource;
export type WritableRecord = RecordMap[WritableResource];
export type Capabilities = { chat?: boolean } & Record<
  | 'create'
  | 'edit'
  | 'remove'
  | 'restore'
  | 'complete'
  | 'log_labor'
  | 'add_note'
  | 'assign',
  boolean
>;
export interface Settings {
  workspaces: { id: string; name: string }[];
  capabilities: Capabilities;
  timezone: string;
}
export interface Brief {
  day: string;
  timezone: string;
  items_today: number;
  completed_today: number;
  open_jobs: number;
  total_labor_hours: number;
  tasks: Task[];
  tasks_has_more: boolean;
}
export interface HistoryEntry {
  id: string;
  action: string;
  actor_id: string;
  occurred_at: string;
  before: Record<string, unknown> | null;
  after: Record<string, unknown>;
}
export interface DialogState {
  resource: WritableResource;
  record?: WritableRecord;
  projectId?: string;
  summaryId?: string;
}
