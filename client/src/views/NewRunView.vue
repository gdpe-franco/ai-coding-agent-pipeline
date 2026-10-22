<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { ApiError, request } from "../api";
import { useProjectsStore } from "../stores/projects";

const projects = useProjectsStore();
const router = useRouter();
const message = ref("");
const projectId = ref("");
const error = ref("");
const submitting = ref(false);

onMounted(() => {
  void projects.fetchAll();
});

async function submit() {
  error.value = "";
  submitting.value = true;
  try {
    const payload: { message: string; project_id?: string } = { message: message.value.trim() };
    if (projectId.value) {
      payload.project_id = projectId.value;
    }
    const run = await request<{ run_id: string }>("/runs", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    await router.push(`/runs/${run.run_id}`);
  } catch (cause) {
    if (cause instanceof ApiError && cause.status === 409) {
      error.value = "That project already has an active run. Finish or close it first.";
    } else {
      error.value = cause instanceof Error ? cause.message : "Could not start the run";
    }
  } finally {
    submitting.value = false;
  }
}
</script>

<template>
  <div class="mx-auto max-w-3xl space-y-5">
    <div>
      <h1 class="text-2xl font-semibold">Describe a static web app</h1>
      <p class="mt-1 text-sm text-slate-400">
        A planner proposes files, a coder writes them, and a reviewer checks the result. You only see a
        completed preview or an honest handoff.
      </p>
    </div>
    <form class="card space-y-4" @submit.prevent="submit">
      <label class="block text-sm">
        <span class="mb-1 block text-slate-400">Request</span>
        <textarea
          v-model="message"
          class="input min-h-32"
          maxlength="8000"
          placeholder="Build a coupon validation page with a Flask-free static UI…"
          required
        />
      </label>
      <label class="block text-sm">
        <span class="mb-1 block text-slate-400">Project</span>
        <select v-model="projectId" class="input">
          <option value="">Let the resolver choose or create a project</option>
          <option v-for="project in projects.projects" :key="project.id" :value="project.id">
            {{ project.title }}
          </option>
        </select>
      </label>
      <p v-if="error" class="text-sm text-rose-300">{{ error }}</p>
      <button class="btn" type="submit" :disabled="submitting || !message.trim()">
        {{ submitting ? "Starting…" : "Start run" }}
      </button>
    </form>
  </div>
</template>
