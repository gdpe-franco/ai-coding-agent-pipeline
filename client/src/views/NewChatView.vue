<script setup lang="ts">
import { ref } from "vue";
import { useRouter } from "vue-router";
import { request } from "../api";

const router = useRouter();
const message = ref("");
const submitting = ref(false);
const error = ref("");
let pendingSubmission: { content: string; key: string } | null = null;

async function createChat() {
  const content = message.value.trim();
  if (!content || submitting.value) return;
  submitting.value = true;
  error.value = "";
  try {
    if (pendingSubmission?.content !== content) {
      pendingSubmission = { content, key: crypto.randomUUID() };
    }
    const created = await request<{ chat_id: string }>("/chats", {
      method: "POST",
      headers: { "Idempotency-Key": pendingSubmission.key },
      body: JSON.stringify({ message: content }),
    });
    pendingSubmission = null;
    await router.push(`/chats/${created.chat_id}`);
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : "Could not start your project";
  } finally {
    submitting.value = false;
  }
}
</script>

<template>
  <section class="card mx-auto max-w-3xl space-y-4">
    <div>
      <h1 class="text-xl font-semibold">Start a new project</h1>
      <p class="mt-1 text-sm text-muted">Your first message creates a project and its chat.</p>
    </div>
    <form class="space-y-3" @submit.prevent="createChat">
      <label for="new-chat-message" class="sr-only">Describe your project</label>
      <textarea
        id="new-chat-message"
        v-model="message"
        class="input min-h-32 w-full"
        maxlength="8000"
        placeholder="What would you like to build?"
        required
        :disabled="submitting"
      />
      <p v-if="error" role="alert" class="text-sm text-danger">{{ error }}</p>
      <div class="flex justify-end">
        <button class="btn" type="submit" :disabled="submitting || !message.trim()">
          {{ submitting ? "Starting…" : "Create project and start" }}
        </button>
      </div>
    </form>
  </section>
</template>
