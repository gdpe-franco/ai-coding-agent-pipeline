<script setup lang="ts">
import { onMounted, onUnmounted, ref } from "vue";
import { request } from "../api";
import StatusBadge from "../components/StatusBadge.vue";
import type {
  Chat,
  ChatSelectionCreate,
  ChatSelectionCreated,
  ChatSelectionChoice,
  ConversationTurn,
  FeatureMode,
  RunStatus,
} from "../types";

const chat = ref<Chat | null>(null);
const draft = ref("");
const projectTitle = ref("");
const startingNewProject = ref(false);
const requestSelection = ref(false);
const sending = ref(false);
const selectionBusy = ref(false);
const error = ref("");
let pollTimer: number | undefined;
let refreshPromise: Promise<void> | undefined;

function refresh(force = false): Promise<void> {
  if (refreshPromise) {
    return force ? refreshPromise.then(() => refresh()) : refreshPromise;
  }
  const pending = (async () => {
    try {
      const lastSequence = chat.value?.turns.at(-1)?.sequence;
      const isClassifying = chat.value?.turns.some(
        (turn) => turn.request_status === "received" || turn.request_status === "classifying",
      );
      const hasActiveRun = chat.value?.turns.some(
        (turn) => turn.run_status === "pending" || turn.run_status === "running",
      );
      const cursor =
        lastSequence === undefined || isClassifying || hasActiveRun ? "" : `?after=${lastSequence}`;
      const latest = await request<Chat>(`/chat${cursor}`);
      if (!chat.value) {
        chat.value = latest;
      } else {
        const knownTurns = new Set(chat.value.turns.map((turn) => turn.id));
        const updatedTurns = new Map(latest.turns.map((turn) => [turn.id, turn]));
        const latestRunStatuses = new Map<string, RunStatus>();
        for (const turn of latest.turns) {
          if (turn.run_id && turn.run_status) {
            latestRunStatuses.set(turn.run_id, turn.run_status);
          }
        }
        chat.value = {
          ...latest,
          turns: [
            ...chat.value.turns.map((turn) => {
              const updated = updatedTurns.get(turn.id) ?? turn;
              const runStatus = turn.run_id ? latestRunStatuses.get(turn.run_id) : undefined;
              return runStatus ? { ...updated, run_status: runStatus } : updated;
            }),
            ...latest.turns.filter((turn) => !knownTurns.has(turn.id)),
          ],
        };
      }
      error.value = "";
    } catch (cause) {
      error.value = cause instanceof Error ? cause.message : "Could not load your chat";
    } finally {
      refreshPromise = undefined;
    }
  })();
  refreshPromise = pending;
  return pending;
}

function unavailableResources(turn: ConversationTurn): string | null {
  const deletedRun =
    turn.run_id === null && (turn.role === "assistant" || turn.request_status === "enqueued");
  if (!deletedRun) return null;
  return turn.project_id
    ? "Related run unavailable (deleted)."
    : "Related project and run unavailable (deleted).";
}

async function send() {
  const message = draft.value.trim();
  if (!message || sending.value) return;

  sending.value = true;
  error.value = "";
  try {
    await request<{ turn_id: string }>("/chat/messages", {
      method: "POST",
      body: JSON.stringify({ message, request_selection: requestSelection.value }),
    });
    draft.value = "";
    requestSelection.value = false;
    await refresh(true);
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : "Could not send your message";
  } finally {
    sending.value = false;
  }
}

function applySelection(turnId: string, patch: Partial<ConversationTurn>) {
  const turn = chat.value?.turns.find((candidate) => candidate.id === turnId);
  if (turn) Object.assign(turn, patch);
  if (chat.value) chat.value.pending_selection = null;
}

function startNewProject() {
  startingNewProject.value = true;
  projectTitle.value = "";
  error.value = "";
}

async function confirmNewProject() {
  const pending = chat.value?.pending_selection;
  const title = projectTitle.value.trim();
  if (!pending || !startingNewProject.value || !title) return;
  await submitSelection({
    context_version: pending.context_version,
    choice: "new_project",
    feature_mode: "new",
    title,
  });
}

async function chooseContext(
  choice: ChatSelectionChoice,
  featureMode: FeatureMode,
  projectId?: string,
) {
  const pending = chat.value?.pending_selection;
  if (!pending) return;
  await submitSelection({
    context_version: pending.context_version,
    choice,
    feature_mode: featureMode,
    project_id: projectId,
  });
}

async function submitSelection(selection: ChatSelectionCreate): Promise<void> {
  const pending = chat.value?.pending_selection;
  if (!pending || selectionBusy.value) return;

  selectionBusy.value = true;
  error.value = "";
  try {
    const result = await request<ChatSelectionCreated>(
      `/chat/messages/${pending.turn_id}/selection`,
      { method: "POST", body: JSON.stringify(selection) },
    );
    applySelection(pending.turn_id, {
      run_id: result.run_id,
      project_id: result.project_id,
      run_status: "pending",
      request_status: "enqueued",
    });
    startingNewProject.value = false;
    projectTitle.value = "";
    await refresh(true);
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : "Could not confirm project selection";
  } finally {
    selectionBusy.value = false;
  }
}

async function cancelSelection(turnId: string) {
  if (selectionBusy.value) return;
  selectionBusy.value = true;
  error.value = "";
  try {
    await request(`/chat/messages/${turnId}/cancel`, { method: "POST" });
    applySelection(turnId, { request_status: "cancelled" });
    startingNewProject.value = false;
    projectTitle.value = "";
    await refresh(true);
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : "Could not cancel project selection";
  } finally {
    selectionBusy.value = false;
  }
}

onMounted(() => {
  void refresh();
  pollTimer = window.setInterval(() => void refresh(), 3000);
});

onUnmounted(() => {
  if (pollTimer !== undefined) window.clearInterval(pollTimer);
});
</script>

<template>
  <section class="card mx-auto flex min-h-[70vh] max-w-4xl flex-col" aria-label="Private chat">
    <header class="border-b border-slate-800 pb-4">
      <h1 class="text-xl font-semibold">Chat</h1>
      <p class="mt-1 text-sm text-slate-400">Your private conversation.</p>
    </header>
    <div class="flex-1 space-y-4 overflow-y-auto py-5" role="log" aria-live="polite">
      <p v-if="!error && chat && chat.turns.length === 0" class="text-sm text-slate-400">
        Your conversation will appear here.
      </p>
      <article
        v-for="turn in chat?.turns ?? []"
        :key="turn.id"
        class="max-w-3xl rounded-lg p-4"
        :class="turn.role === 'user' ? 'ml-auto bg-indigo-950/70' : 'bg-slate-900'"
        :aria-label="turn.role === 'user' ? 'Your message' : `${turn.role} message`"
      >
        <p class="whitespace-pre-wrap text-sm leading-6">{{ turn.content }}</p>
        <p
          v-if="turn.request_status === 'received' || turn.request_status === 'classifying'"
          class="mt-2 text-xs text-slate-400"
          role="status"
        >
          Checking the current project and feature…
        </p>
        <div
          v-if="turn.role === 'user' && turn.run_status"
          class="mt-2 flex items-center gap-2 text-xs text-slate-400"
          role="status"
        >
          Run <StatusBadge :status="turn.run_status" />
        </div>
        <p
          v-if="unavailableResources(turn)"
          class="mt-2 text-xs text-slate-400"
          role="status"
        >
          {{ unavailableResources(turn) }}
        </p>
        <section
          v-if="chat?.pending_selection?.turn_id === turn.id"
          class="mt-4 space-y-3 border-t border-slate-700 pt-4"
          aria-label="Choose a project"
        >
          <div>
            <h2 class="text-sm font-semibold">Choose a project</h2>
            <p class="mt-1 text-xs text-slate-400">Confirm the project and feature before this request starts.</p>
          </div>
          <p v-if="chat.pending_selection.reason" class="text-sm text-amber-200">
            {{ chat.pending_selection.reason }}
          </p>
          <button
            v-if="chat.selected_project_id"
            class="btn-secondary"
            type="button"
            :disabled="selectionBusy"
            @click="chooseContext('continue_current', 'continue')"
          >
            Continue current context
          </button>
          <p v-if="chat.pending_selection.projects.length === 0" class="text-sm text-slate-400">
            You have no projects yet.
          </p>
          <ul v-else class="space-y-3 text-sm text-slate-300" aria-label="Your projects">
            <li v-for="project in chat.pending_selection.projects" :key="project.id">
              <div class="flex flex-wrap items-center justify-between gap-2">
                <span>{{ project.title }}</span>
                <div class="flex gap-2">
                  <button
                    class="btn-secondary"
                    type="button"
                    :disabled="selectionBusy"
                    @click="chooseContext('existing_project', 'continue', project.id)"
                  >
                    Continue project
                  </button>
                  <button
                    class="btn-secondary"
                    type="button"
                    :disabled="selectionBusy"
                    @click="chooseContext('existing_project', 'new', project.id)"
                  >
                    New feature
                  </button>
                </div>
              </div>
            </li>
          </ul>
          <button
            v-if="!startingNewProject"
            class="btn-secondary"
            type="button"
            :disabled="selectionBusy"
            @click="startNewProject"
          >
            New project
          </button>
          <form v-else class="space-y-3" @submit.prevent="confirmNewProject">
            <label :for="`project-title-${turn.id}`" class="block text-sm">
              <span class="mb-1 block text-slate-400">Project title</span>
              <input
                :id="`project-title-${turn.id}`"
                v-model="projectTitle"
                class="input"
                maxlength="200"
                required
                :disabled="selectionBusy"
              />
            </label>
            <button class="btn" type="submit" :disabled="selectionBusy || !projectTitle.trim()">
              {{ selectionBusy ? "Creating…" : "Create project and start" }}
            </button>
          </form>
          <button
            class="text-sm text-slate-400 hover:text-white"
            type="button"
            :disabled="selectionBusy"
            @click="cancelSelection(turn.id)"
          >
            Cancel request
          </button>
        </section>
        <div
          v-if="turn.run_id || turn.project_id"
          class="mt-3 flex flex-wrap gap-3 border-t border-slate-700 pt-3 text-xs"
        >
          <RouterLink
            v-if="turn.run_id"
            :to="`/runs/${turn.run_id}`"
            class="text-indigo-300 hover:text-indigo-200"
          >
            View related run
          </RouterLink>
          <RouterLink
            v-if="turn.project_id"
            :to="`/projects/${turn.project_id}`"
            class="text-indigo-300 hover:text-indigo-200"
          >
            View related project
          </RouterLink>
        </div>
      </article>
    </div>
    <form class="border-t border-slate-800 pt-4" @submit.prevent="send">
      <label for="chat-message" class="sr-only">Message</label>
      <label
        v-if="chat?.selected_project_id"
        class="mb-3 flex items-center gap-2 text-sm text-slate-400"
      >
        <input
          v-model="requestSelection"
          type="checkbox"
          :disabled="sending || selectionBusy || Boolean(chat?.pending_selection)"
        />
        Choose the project or feature for this request
      </label>
      <div class="flex items-end gap-3">
        <textarea
          id="chat-message"
          v-model="draft"
          class="input min-h-20 flex-1 resize-y"
          maxlength="8000"
          placeholder="Write a message"
          :disabled="sending || selectionBusy || Boolean(chat?.pending_selection)"
        />
        <button
          class="btn"
          type="submit"
          :disabled="sending || selectionBusy || Boolean(chat?.pending_selection) || !draft.trim()"
        >
          {{ sending ? "Sending…" : "Send" }}
        </button>
      </div>
      <p v-if="error" class="mt-2 text-sm text-rose-300" role="alert">{{ error }}</p>
    </form>
  </section>
</template>
