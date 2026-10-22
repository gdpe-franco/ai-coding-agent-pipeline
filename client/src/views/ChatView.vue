<script setup lang="ts">
import { onMounted, onUnmounted, ref } from "vue";
import { request } from "../api";
import type { Chat, ConversationTurn } from "../types";

const chat = ref<Chat | null>(null);
const draft = ref("");
const projectTitle = ref("");
const startingNewProject = ref(false);
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
      const cursor = lastSequence === undefined ? "" : `?after=${lastSequence}`;
      const latest = await request<Chat>(`/chat${cursor}`);
      if (!chat.value) {
        chat.value = latest;
      } else {
        const knownTurns = new Set(chat.value.turns.map((turn) => turn.id));
        chat.value = {
          ...latest,
          turns: [
            ...chat.value.turns,
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

async function send() {
  const message = draft.value.trim();
  if (!message || sending.value) return;

  sending.value = true;
  error.value = "";
  try {
    await request<{ turn_id: string }>("/chat/messages", {
      method: "POST",
      body: JSON.stringify({ message }),
    });
    draft.value = "";
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
  if (!pending || !startingNewProject.value || !title || selectionBusy.value) return;

  selectionBusy.value = true;
  error.value = "";
  try {
    const selection = await request<{ run_id: string; project_id: string }>(
      `/chat/messages/${pending.turn_id}/selection`,
      {
        method: "POST",
        body: JSON.stringify({
          context_version: pending.context_version,
          choice: "new_project",
          feature_mode: "new",
          title,
        }),
      },
    );
    applySelection(pending.turn_id, {
      run_id: selection.run_id,
      project_id: selection.project_id,
      request_status: "enqueued",
    });
    startingNewProject.value = false;
    projectTitle.value = "";
    await refresh(true);
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : "Could not create your project";
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
        <section
          v-if="chat?.pending_selection?.turn_id === turn.id"
          class="mt-4 space-y-3 border-t border-slate-700 pt-4"
          aria-label="Choose a project"
        >
          <div>
            <h2 class="text-sm font-semibold">Choose a project</h2>
            <p class="mt-1 text-xs text-slate-400">This request needs a project. Create one to start it now.</p>
          </div>
          <p v-if="chat.pending_selection.projects.length === 0" class="text-sm text-slate-400">
            You have no projects yet.
          </p>
          <ul v-else class="space-y-1 text-sm text-slate-300" aria-label="Your projects">
            <li v-for="project in chat.pending_selection.projects" :key="project.id">
              {{ project.title }}
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
