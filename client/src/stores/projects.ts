import { defineStore } from "pinia";
import { request } from "../api";
import type { Project } from "../types";

export const useProjectsStore = defineStore("projects", {
  state: () => ({
    projects: [] as Project[],
    current: null as Project | null,
    loading: false,
    error: "",
  }),
  actions: {
    async fetchAll() {
      this.loading = true;
      this.error = "";
      try {
        this.projects = await request<Project[]>("/projects");
      } catch (error) {
        this.error = error instanceof Error ? error.message : "Could not load projects";
      } finally {
        this.loading = false;
      }
    },
    async fetch(id: string) {
      this.current = await request<Project>(`/projects/${id}`);
      return this.current;
    },
    async create(title: string, summary: string) {
      const project = await request<Project>("/projects", {
        method: "POST",
        body: JSON.stringify({ title, summary }),
      });
      this.projects = [project, ...this.projects];
      return project;
    },
    async remove(id: string) {
      await request(`/projects/${id}`, { method: "DELETE" });
      this.projects = this.projects.filter((project) => project.id !== id);
    },
  },
});
