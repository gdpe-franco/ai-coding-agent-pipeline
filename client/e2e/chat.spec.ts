import { expect, test } from "@playwright/test";

type Turn = {
  id: string;
  role: "user" | "assistant";
  content: string;
  sequence: number;
  project_id: string | null;
  run_id: string | null;
  run_status: string | null;
  request_status: string | null;
  created_at: string;
};

test("one chat selects, generates, hands off, switches projects, and survives reload", async ({
  page,
}) => {
  const turns: Turn[] = [];
  const projects = new Map<string, { id: string; title: string }>();
  const runs = new Map<string, Record<string, unknown>>();
  const selections: Array<{ choice: string; project_id: string | null }> = [];
  let pendingSelection: Record<string, unknown> | null = null;
  let selectedProjectId: string | null = null;
  let activeFeatureRunId: string | null = null;
  let contextVersion = 0;
  let turnNumber = 0;
  let projectNumber = 0;
  let runNumber = 0;
  let lastNormalRunProjectId: string | null = null;

  function chat() {
    return {
      conversation_id: "conversation-1",
      context_version: contextVersion,
      selected_project_id: selectedProjectId,
      active_feature_run_id: activeFeatureRunId,
      pending_selection: pendingSelection,
      turns: structuredClone(turns),
    };
  }

  function appendAssistant(projectId: string, runId: string, status: string, message: string) {
    turns.push({
      id: `turn-${++turnNumber}`,
      role: "assistant",
      content: message,
      sequence: turns.length + 1,
      project_id: projectId,
      run_id: runId,
      run_status: null,
      request_status: null,
      created_at: "2026-10-23T12:00:00Z",
    });
    const run = runs.get(runId);
    if (run) run.status = status;
  }

  function createRun(turn: Turn, projectId: string, status: "completed" | "awaiting_user") {
    const runId = `run-${++runNumber}`;
    const message =
      status === "awaiting_user"
        ? "The reviewer stopped with an honest handoff."
        : "Generated files passed review and are ready to preview.";
    turn.project_id = projectId;
    turn.run_id = runId;
    turn.run_status = status;
    turn.request_status = "enqueued";
    if (status === "awaiting_user") activeFeatureRunId = runId;
    else activeFeatureRunId = null;
    runs.set(runId, {
      id: runId,
      project_id: projectId,
      message: turn.content,
      status,
      attempts_used: 1,
      attempts_total: runNumber,
      round: 1,
      user_guidance: null,
      plan: {
        id: `plan-${runId}`,
        project_id: projectId,
        summary: `Plan for ${turn.content}`,
        steps: [
          {
            id: `step-${runId}`,
            name: "Coupon page",
            file_path: "coupon.html",
            file_type: "html",
            description: "Build the coupon page.",
          },
        ],
      },
      latest_code: [
        {
          file_path: "coupon.html",
          content: "<h1>Coupon app</h1>",
          checksum: "a".repeat(64),
          file_type: "html",
        },
      ],
      feedback: {
        verdict: status === "completed" ? "approve" : "escalate",
        summary: status === "completed" ? "Looks good." : "Needs guidance.",
        issues: [],
      },
      checks: {
        file_structure_ok: true,
        js_syntax_ok: true,
        files_checked: 1,
        syntax_checked: [],
        issues: [],
      },
      chat_reply: message,
      error: null,
    });
    appendAssistant(projectId, runId, status, message);
  }

  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const method = request.method();
    const path = new URL(request.url()).pathname.replace("/api/v1", "");
    const reply = (body: unknown, status = 200) =>
      route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });

    if (path === "/auth/login" && method === "POST") {
      await reply({ access_token: "browser-test-token", token_type: "bearer" });
    } else if (path === "/users/me") {
      await reply({ id: "user-1", email: "browser@example.com" });
    } else if (path === "/chat" && method === "GET") {
      await reply(chat());
    } else if (path === "/chat/messages" && method === "POST") {
      const body = request.postDataJSON() as { message: string; request_selection: boolean };
      const turn: Turn = {
        id: `turn-${++turnNumber}`,
        role: "user",
        content: body.message,
        sequence: turns.length + 1,
        project_id: selectedProjectId,
        run_id: null,
        run_status: null,
        request_status: "needs_selection",
        created_at: "2026-10-23T12:00:00Z",
      };
      turns.push(turn);
      if (body.request_selection || selectedProjectId === null) {
        pendingSelection = {
          turn_id: turn.id,
          context_version: contextVersion,
          projects: [...projects.values()],
          reason: "Confirm the project and feature for this request.",
        };
      } else {
        lastNormalRunProjectId = selectedProjectId;
        createRun(turn, selectedProjectId, "awaiting_user");
      }
      await reply({ turn_id: turn.id }, 202);
    } else if (/^\/chat\/messages\/[^/]+\/selection$/.test(path) && method === "POST") {
      const body = request.postDataJSON() as {
        choice: string;
        project_id?: string;
        title?: string;
      };
      selections.push({ choice: body.choice, project_id: body.project_id ?? null });
      const turnId = path.split("/")[3];
      const turn = turns.find((candidate) => candidate.id === turnId);
      if (!turn) {
        await reply({ detail: "Unknown turn" }, 404);
        return;
      }
      let projectId = body.project_id;
      if (body.choice === "new_project") {
        projectId = `project-${++projectNumber}`;
        projects.set(projectId, { id: projectId, title: body.title ?? "Untitled" });
      }
      if (!projectId || !projects.has(projectId)) {
        await reply({ detail: "Unknown project" }, 404);
        return;
      }
      selectedProjectId = projectId;
      contextVersion += 1;
      pendingSelection = null;
      createRun(turn, projectId, "completed");
      await reply({ run_id: `run-${runNumber}`, project_id: projectId, context_version: contextVersion });
    } else if (path.startsWith("/projects/") && path.endsWith("/preview-url")) {
      const projectId = path.split("/")[2];
      await reply({ url: `https://preview.example.test/preview/${projectId}/index.html` });
    } else if (path.startsWith("/projects/") && path.endsWith("/conversation")) {
      const projectId = path.split("/")[2];
      await reply(turns.filter((turn) => turn.project_id === projectId));
    } else if (path.startsWith("/projects/") && method === "GET") {
      const projectId = path.split("/")[2];
      const project = projects.get(projectId);
      await reply(
        project
          ? { ...project, summary: "", preview_id: `preview-${projectId}`, status: "active", created_at: "2026-10-23T12:00:00Z", updated_at: "2026-10-23T12:00:00Z" }
          : { detail: "Not found" },
        project ? 200 : 404,
      );
    } else if (/^\/runs\/[^/]+\/events$/.test(path)) {
      await reply([]);
    } else if (/^\/runs\/[^/]+$/.test(path)) {
      await reply(runs.get(path.split("/")[2]) ?? { detail: "Not found" }, runs.has(path.split("/")[2]) ? 200 : 404);
    } else {
      await reply({ detail: `Unhandled browser-test request: ${method} ${path}` }, 404);
    }
  });
  await page.route("https://preview.example.test/**", (route) =>
    route.fulfill({
      contentType: "text/html",
      body: "<!doctype html><html><body><h1>Generated preview</h1></body></html>",
    }),
  );

  await page.goto("/login");
  await page.getByLabel("Email").fill("browser@example.com");
  await page.getByLabel("Password").fill("test-password");
  await page.locator("form").getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Chat" })).toBeVisible();

  await page.getByRole("textbox", { name: "Message" }).fill("Build a coupon validator");
  await page.getByRole("button", { name: "Send" }).click();
  await page.getByRole("button", { name: "New project" }).click();
  await page.getByLabel("Project title").fill("Coupon validator");
  await page.getByRole("button", { name: "Create project and start" }).click();
  await expect(page.getByText("Generated files passed review and are ready to preview.")).toBeVisible();

  await page.getByRole("link", { name: "View related run" }).first().click();
  await expect(page.getByText("coupon.html", { exact: true }).first()).toBeVisible();
  await expect(
    page.frameLocator('iframe[title="Generated app preview"]').getByRole("heading", {
      name: "Generated preview",
    }),
  ).toBeVisible();
  await page.getByRole("link", { name: "Chat" }).click();

  await page.getByRole("textbox", { name: "Message" }).fill("Add a score counter");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByText("Handoff — needs you")).toBeVisible();
  await page.getByRole("link", { name: "View related run" }).last().click();
  await expect(
    page.getByRole("heading", { name: "The reviewer stopped with an honest handoff" }),
  ).toBeVisible();
  await page.getByRole("link", { name: "Chat" }).click();

  await page.getByRole("checkbox").check();
  await page.getByRole("textbox", { name: "Message" }).fill("Make a Flappy Bird game");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByRole("heading", { name: "Choose a project" })).toBeVisible();
  await page.getByRole("button", { name: "New project" }).click();
  await page.getByLabel("Project title").fill("Flappy Bird");
  await page.getByRole("button", { name: "Create project and start" }).click();

  await page.getByRole("checkbox").check();
  await page.getByRole("textbox", { name: "Message" }).fill("Return to the coupon validator");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByRole("heading", { name: "Choose a project" })).toBeVisible();
  const couponOption = page.getByRole("listitem").filter({ hasText: "Coupon validator" });
  await couponOption.getByRole("button", { name: "Continue project" }).click();
  await expect(page.getByText("Return to the coupon validator")).toBeVisible();
  expect(selections.map((selection) => selection.choice)).toEqual([
    "new_project",
    "new_project",
    "existing_project",
  ]);

  await page.reload();
  await expect(page.getByText("Build a coupon validator")).toBeVisible();
  await expect(page.getByText("Make a Flappy Bird game")).toBeVisible();
  await expect(page.getByText("Return to the coupon validator")).toBeVisible();
  await page.getByRole("textbox", { name: "Message" }).fill("Add one more coupon check");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByText("Add one more coupon check")).toBeVisible();
  expect(lastNormalRunProjectId).toBe("project-1");
  await page.getByRole("link", { name: "View related project" }).last().click();
  await expect(page.getByRole("heading", { name: "Coupon validator" })).toBeVisible();
});
