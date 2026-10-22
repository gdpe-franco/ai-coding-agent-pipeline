<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { useProjectsStore } from "../stores/projects";

const projects = useProjectsStore();
const router = useRouter();
const title = ref("");
const summary = ref("");
const error = ref("");

onMounted(() => {
  void projects.fetchAll();
});

async function create() {
  error.value = "";
  if (!title.value.trim()) {
    return;
  }
  try {
    const project = await projects.create(title.value.trim(), summary.value.trim());
    title.value = "";
    summary.value = "";
    await router.push(`/projects/${project.id}`);
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : "Could not create project";
  }
}

async function removeProject(id: string) {
  if (!window.confirm("Delete this project, its files, and its history?")) {
    return;
  }
  await projects.remove(id);
}
</script>

<template>
  <div class="space-y-6">
    <section class="card space-y-3">
      <h1 class="text-xl font-semibold">New project</h1>
      <form class="grid gap-3 md:grid-cols-[1fr_2fr_auto]" @submit.prevent="create">
        <input v-model="title" class="input" placeholder="Project title" required />
        <input v-model="summary" class="input" placeholder="Optional short summary" />
        <button class="btn" type="submit">Create</button>
      </form>
      <p v-if="error" class="text-sm text-rose-300">{{ error }}</p>
    </section>

    <section class="space-y-3">
      <div class="flex items-center justify-between">
        <h2 class="text-lg font-semibold">Your projects</h2>
        <button class="btn-secondary" :disabled="projects.loading" @click="projects.fetchAll()">Refresh</button>
      </div>
      <p v-if="projects.error" class="text-sm text-rose-300">{{ projects.error }}</p>
      <p v-if="!projects.loading && projects.projects.length === 0" class="text-sm text-slate-400">
        No projects yet. Create one or describe a brand-new app from the run page.
      </p>
      <article v-for="project in projects.projects" :key="project.id" class="card flex items-start justify-between gap-4">
        <button class="min-w-0 text-left" @click="router.push(`/projects/${project.id}`)">
          <h3 class="truncate text-base font-semibold text-indigo-300">{{ project.title }}</h3>
          <p class="mt-1 line-clamp-2 text-sm text-slate-400">{{ project.summary || "No summary yet." }}</p>
          <p class="mt-2 text-xs text-slate-500">Updated {{ new Date(project.updated_at).toLocaleString() }}</p>
        </button>
        <button class="text-sm text-rose-300 hover:text-rose-200" @click="removeProject(project.id)">Delete</button>
      </article>
    </section>
  </div>
</template>
