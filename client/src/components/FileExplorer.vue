<script setup lang="ts">
import { computed, ref, watch } from "vue";

type Branch = { name: string; path: string; directory: boolean; children: Map<string, Branch> };
type VisibleBranch = { branch: Branch; depth: number };

const props = withDefaults(
  defineProps<{ title: string; files: string[]; loadFile: (path: string) => Promise<string> }>(),
  { files: () => [] },
);

const selectedPath = ref("");
const content = ref("");
const expanded = ref(new Set<string>());
const loading = ref(false);
const error = ref("");

const tree = computed(() => {
  const root = new Map<string, Branch>();
  const directories = new Set<string>();
  for (const path of props.files) {
    const parts = path.split("/");
    let children = root;
    let currentPath = "";
    for (const [index, name] of parts.entries()) {
      currentPath = currentPath ? `${currentPath}/${name}` : name;
      const directory = index < parts.length - 1;
      let branch = children.get(name);
      if (!branch) {
        branch = { name, path: currentPath, directory, children: new Map() };
        children.set(name, branch);
      }
      if (directory) {
        directories.add(currentPath);
        children = branch.children;
      }
    }
  }
  const sort = (branches: Map<string, Branch>): Branch[] =>
    [...branches.values()]
      .sort((left, right) => Number(right.directory) - Number(left.directory) || left.name.localeCompare(right.name))
      .map((branch) => ({ ...branch, children: branch.children }));
  return { branches: sort(root), directories };
});

const visibleBranches = computed(() => {
  const result: VisibleBranch[] = [];
  const append = (branches: Branch[], depth: number) => {
    for (const branch of branches) {
      result.push({ branch, depth });
      if (branch.directory && expanded.value.has(branch.path)) {
        append([...branch.children.values()].sort((left, right) => Number(right.directory) - Number(left.directory) || left.name.localeCompare(right.name)), depth + 1);
      }
    }
  };
  append(tree.value.branches, 0);
  return result;
});

watch(
  () => tree.value.directories,
  (directories) => {
    expanded.value = new Set(directories);
  },
  { immediate: true },
);

watch(
  () => props.files,
  () => {
    selectedPath.value = "";
    content.value = "";
  },
  { deep: true },
);

function toggle(branch: Branch) {
  if (!branch.directory) return;
  const next = new Set(expanded.value);
  if (next.has(branch.path)) next.delete(branch.path);
  else next.add(branch.path);
  expanded.value = next;
}

async function selectFile(path: string) {
  selectedPath.value = path;
  loading.value = true;
  error.value = "";
  content.value = "";
  try {
    content.value = await props.loadFile(path);
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : "Could not read this file";
  } finally {
    loading.value = false;
  }
}
</script>

<template>
  <section class="card space-y-3" :aria-label="title">
    <header class="flex flex-wrap items-center justify-between gap-3">
      <h2 class="text-lg font-semibold">{{ title }}</h2>
      <slot name="actions" />
    </header>
    <p v-if="files.length === 0" class="text-sm text-muted">No files yet.</p>
    <div v-else class="grid min-h-64 gap-4 lg:grid-cols-[minmax(12rem,0.8fr)_minmax(0,1.5fr)]">
      <nav class="max-h-[32rem] overflow-auto border-r border-line pr-3" :aria-label="`${title} file tree`">
        <ul class="space-y-1">
          <li v-for="{ branch, depth } in visibleBranches" :key="branch.path">
            <button
              class="flex w-full items-center gap-2 truncate rounded px-2 py-1 text-left text-sm hover:bg-hover"
              :class="selectedPath === branch.path ? 'bg-hover text-accent-hover' : 'text-paper-dim'"
              :style="{ paddingLeft: `${0.5 + depth * 1}rem` }"
              :aria-label="`${branch.directory ? (expanded.has(branch.path) ? 'Collapse' : 'Expand') : 'Open'} ${branch.name}`"
              :aria-expanded="branch.directory ? expanded.has(branch.path) : undefined"
              @click="branch.directory ? toggle(branch) : selectFile(branch.path)"
            >
              <span class="w-4 shrink-0 font-mono text-xs" aria-hidden="true">
                {{ branch.directory ? (expanded.has(branch.path) ? "▾" : "▸") : "·" }}
              </span>
              <span class="truncate" :class="branch.directory ? 'font-medium' : 'font-mono text-xs'">
                {{ branch.name }}
              </span>
            </button>
          </li>
        </ul>
      </nav>
      <div class="min-w-0">
        <p v-if="selectedPath" class="mb-2 truncate font-mono text-xs text-faint">{{ selectedPath }}</p>
        <p v-if="loading" role="status" class="text-sm text-muted">Opening file…</p>
        <p v-else-if="error" role="alert" class="text-sm text-danger">{{ error }}</p>
        <p v-else-if="!selectedPath" class="text-sm text-faint">Choose a file to inspect it.</p>
        <pre v-else class="max-h-[32rem] overflow-auto rounded-lg bg-ink p-3 text-xs text-paper-dim"><code>{{ content }}</code></pre>
      </div>
    </div>
  </section>
</template>
