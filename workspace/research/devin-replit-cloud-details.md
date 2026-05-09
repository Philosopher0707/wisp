# AI Coding Agent Landscape: Comprehensive Reference (May 2026)

Bullet-heavy reference format. Every detail cataloged.

---

## Part A: Devin (cognition.ai)

### 1. Full Autonomous Agent

- Built by **Cognition Labs** (now "Cognition"), launched March 2024, generally available December 2024
- Operates in its own **sandboxed cloud environment** with shell, code editor, and browser
- Full SDLC loop: plan --> code --> debug --> test --> deploy --> open PR
- Does NOT require you to be present -- assign a task, it works in the background, returns a PR
- Devin 2.2 (Feb 2026): biggest update since launch
  - End-to-end testing with **computer use** (access to Linux desktop, launches desktop apps)
  - "Devin Review Autofix" -- plans, codes, reviews own output, catches issues, fixes them BEFORE you see PR
  - 3x faster startup
  - Fully redesigned UI unifying planning --> code review
- SWE-bench score: ~67% PR merge rate
- Real-world success rates: ~78% on bug fixes with clear repro steps, ~82%+ on test writing, but only ~15% on diverse novel tasks (Answer.AI eval)

### 2. Devin's IDE

- **Browser-based IDE** -- no local install needed
- Shell access (inside its sandbox)
- Built-in browser for testing and previewing
- Planner -- generates implementation plans before coding (editable by user)
- "Interactive Planning" (2.0+) -- proactive codebase research, detailed editable plans before execution
- Redesigned in 2.2 to unify planning --> code review in single view
- Visual QA with full browser and desktop use
- Screen recording sessions sent back for review

### 3. Devin's Knowledge

- **Memory across sessions** -- learns from past work, retains context
- **Repository knowledge** -- indexes and understands entire codebases
- **Devin Search** (2.0+) -- agentic codebase Q&A with cited code references
  - "Deep Mode" for complex queries (multi-step research)
- **Devin Wiki** -- auto-indexes repos every few hours
  - Auto-generates architecture diagrams and documentation
  - Generates `DeepWiki` for legacy codebases
- **Knowledge Management** -- deduplicate, consolidate, create org knowledge entries
- **Learnings** -- each session can produce reusable knowledge entries for future sessions

### 4. Devin's PR Workflow

- Fully automated PR creation from task description
- **Devin Review** -- standalone AI code review tool (free for open source repos)
- **Autofix loop** -- catches and fixes issues before PR is even opened
- PR Review & Visual QA -- auto-identify bugs, intelligent code diff organization
- Auto-fixes CI failures
- Sends PR for human review; you review and merge
- Review feedback handling -- Devin can respond to PR review comments and iterate

### 5. Devin's Tools

- Shell access (isolated sandbox)
- Code editor
- Full browser (for testing, visual QA, research)
- Linux desktop access (as of 2.2)
- GitHub, Linear, Jira integration
- Slack integration (assign tasks via Slack, receive PRs)
- Microsoft Teams integration
- Datadog, Sentry (incident investigation)
- AWS, Azure, Snowflake, Databricks
- PostgreSQL, MongoDB, Stripe
- Confluence, Notion, Airtable, Asana, Google Drive
- **MCP support** -- any MCP server can extend Devin's tools
- **Playbooks** -- turn successful sessions into reusable, shareable macros
- **Scheduled chores** -- recurring sessions for QA, release notes, etc.

### 6. Devin's Communication

- **Slack integration**
  - Tag `@Devin` in any channel/thread to start sessions
  - Keywords: `!ask`, `!deep`, `!fast`, `!dana` (data analyst), `mute`/`unmute`, `sleep`, `archive`, `EXIT`
  - Audio messages support -- responds to voice clips
  - Dedicated `#devin-runs` channel for centralized collaboration
  - Custom bot name configurable
  - Private status updates per-run
  - Macro/playbook attachment via `![macro_name]`
- **Microsoft Teams** integration
- Status updates and handoffs -- Devin reports progress, delivers PRs, can hand off between sessions
- Email notifications on run completion

### 7. Devin Enterprise

- **Organization management**: multiple orgs to segment teams/projects
- **User roles**: Enterprise Admins, Organization Admins, Members
  - Custom Roles & RBAC at both org and enterprise levels
  - IdP group integration -- auto-assign roles based on SSO group membership
- **SSO**: Okta, Entra (Azure AD), SAML, OIDC
- **Source code integrations**: GitHub, GH Enterprise, GitLab, Bitbucket, Azure DevOps
- **VPC deployment** (custom)
- **Audit logs**, dedicated support
- **Usage-based billing** managed centrally
- **Managed Devins** -- coordinator Devin breaks down large tasks, delegates to parallel child sessions
  - Each child runs in isolated VM with specific prompts, playbooks, tags, ACU limits
  - Coordinator can message, monitor, sleep/terminate child sessions
  - Use cases: parallel migrations, batch test coverage, cross-service playbooks
  - Permission: `UseDevinExpert` role
- Enterprise adoption: Goldman Sachs (12,000 engineers), Santander, Nubank, MongoDB, Ramp
- **Windsurf acquisition** (July 2025) -- Cognition bought agentic IDE (formerly Codeium)
- Nubank case study: 8-12x engineering time efficiency, 20x cost savings

### 8. Pricing

| Plan | Price | Details |
|------|-------|---------|
| **Free** | $0/mo | Limited access, getting-started tier |
| **Pro (Core)** | $20/mo | Pay-as-you-go ~$2.25/ACU, up to 10 concurrent sessions |
| **Max** | $200/mo | Larger quota for heavy individual use |
| **Teams** | $500/mo (min $80 spend) | 250 ACUs included, unlimited seats, Slack/IDE/API access |
| **Enterprise** | Custom | VPC, SSO, audit logs, dedicated support, custom data controls |

- **ACU (Agent Compute Unit)** = ~15 minutes of Devin actively working
- Simple tasks: 1-3 ACUs; complex migrations: 10+ ACUs
- Equivalent to ~$8-9/hour of agent time
- Devin Review tool: free for open source repos
- Devin for Terminal: `curl -fsSL https://cli.devin.ai/install.sh | bash`

### 9. Unique Capabilities vs IDE Tools

| Aspect | Devin | IDE Tools (Cursor/Copilot) |
|--------|-------|---------------------------|
| **Autonomy** | Fully autonomous, assign & walk away | Pair programming, you are present |
| **Environment** | Own sandboxed cloud VM | Runs in your local IDE |
| **Timing** | Works while you sleep | Works while you're coding |
| **Human interaction** | Minimal after task assignment | Constant inline interaction |
| **Task scope** | Entire features/migrations | Line-level completions, chat |
| **Parallelism** | Fleets of managed Devins | Single developer + assistant |
| **Desktop apps** | Tests desktop apps (Linux desktop) | No desktop app testing |
| **Self-review** | Reviews own output, auto-fixes before PR | You review everything |
| **Learning** | Persistent organizational knowledge | No cross-session learning |
| **Slack-native** | Tasks assigned via Slack message | Not Slack-native |

### 10. Devin's Weaknesses

- **Ambiguous/novel tasks**: ~15-25% success rate without human intervention
- **Architectural decisions**: no real judgment; follows patterns blindly
- **Complex debugging**: gets stuck in rabbit holes on race conditions, distributed systems
- **"Last 30%" problem**: delivers ~70% of features, misses edge cases and polish
- **Performance optimization**: produces functional but not fast code
- **Security awareness**: can introduce vulnerabilities; all output needs human review
- **Unpredictable costs**: ACU consumption hard to forecast; can get stuck in billing loops
- **Code quality**: described as "intern-level" with variability

---

## Part B: Replit Agent (replit.com)

### 1. Replit Agent -- The Agent Loop

- Natural language --> full working application
- **Agent 3** (Sept 2025) -- most autonomous version
  - "10x more autonomous, 3x faster, 10x more cost-effective than Computer Use models"
  - App Testing: agent tests apps in real browser (clicks buttons, checks forms, APIs, login flows), auto-fixes issues
  - Max Autonomy (beta): runs up to 200+ minutes with self-supervision and task management
  - Builds other agents & automations (Telegram bots, Slack agents, scheduled email summaries, Notion/Linear integrations)
  - New app creation flow: choose full-stack or frontend-only before backend
- Multi-step: plans first, then builds, tests, iterates automatically
- **Plan Mode**: generates plan before implementation, editable by user
- **Task Planning (Kanban)**: visual task board for tracking multi-feature builds
- **Auto-continue**: keeps going until task is complete, with full visibility
- **Effort-based billing** -- you pay based on request complexity, not per-step
  - Small bug fixes < full feature builds
  - Complex work bundles into one checkpoint per request
  - Charges visible in real-time
  - Spending controls: usage alerts, hard budget caps, credit packs

### 2. Replit IDE

- **Browser-based**, full cloud IDE -- no local install
- **Collaborative**: multiplayer editing (like Google Docs for code)
- **Terminal access**: real cloud VM with shell
- **Design Canvas**: visual UI builder, convert designs to artifacts
- File tree, editor, terminal, preview all in one browser tab
- Full IDE visible to developer (higher learning curve than Bolt/Lovable for non-devs)
- Works on Chromebooks, any browser

### 3. Replit AI (Ghostwriter)

- **Ghostwriter chat**: inline AI chat for code generation, explanation, editing
- **Complete Code**: AI-powered autocomplete
- **Generate**: create functions, files, components from description
- **Explain**: AI explains any code block
- **Edit**: select code, describe change, AI applies it
- Available across all supported languages (50+)
- **Lite Build**: quick generation mode (available on free tier)
- **Full Build**: comprehensive generation with testing (Core+)
- **Turbo Mode**: prioritized, faster generation (Pro only)

### 4. Replit Deployments

- **Auto-deploy**: one-click from development to production
- **Hosting**: Replit hosts your app on their infrastructure
- **Custom domains**: connect your own domain
- **Private publishing**: available on all plans (was Pro/Enterprise only, now Starter too)
- **App Monitoring** (2026): email alerts when published app goes down
  - Agent can sift through logs and databases to diagnose root causes
- **Security Center 2.0**: bulk remediation of critical vulnerabilities across all projects
- **External Access Tokens**: let trusted external services securely access private apps

### 5. Replit Bounties

- **Freelance marketplace** for AI-assisted development
- Post a bounty (task/project), other Replit users (with AI) complete it
- AI assistance accelerates bounty completion
- Creates gig economy for AI-augmented developers
- Revenue stream and community engagement for Replit

### 6. Replit Teams

- **Education**: widely used in CS education, classroom features
- **Team features**: 
  - 5 collaborators (Core), 15 collaborators (Pro), 50 viewers (Pro)
  - Shared projects, multiplayer editing
  - Background tasks (1 on Core, 10 on Pro): scheduled agents running in background
- **Enterprise**: SSO/SAML, single-tenant, VPC peering, custom groups, region selection, dedicated support

### 7. Pricing

| Plan | Monthly | Annual (per mo) | Key Features |
|------|---------|-----------------|--------------|
| **Starter** | Free | Free | Daily Agent credits (capped), Lite build only, 1 published app (30-day), Design Canvas |
| **Core** | $25/mo | $20/mo | $25 monthly credits, Full build, Plan Mode, Connectors, all artifact types, unlimited apps, 5 collaborators, 1 background task |
| **Pro** | $100/mo | $95/mo | $100 monthly credits, Turbo mode, 10 background tasks, 15 collaborators, 50 viewers, premium support, 28-day DB restore |
| **Enterprise** | Custom | Custom | All Pro + SSO/SAML, single-tenant, VPC peering, custom groups |

- Effort-based billing: complex tasks cost more credits, but one checkpoint per request
- Spending controls: usage alerts, hard budget caps, credit packs

### 8. Templates

- **Pre-built templates**: extensive library for quick starts
- **Remix (fork)**: fork any existing public Replit project as starting point
- Templates cover: web apps, APIs, bots, data science, games, more
- 50+ languages supported (Python, Node.js, Go, Ruby, Java, Rust, C++, etc.)

### 9. Mobile App

- **No dedicated mobile IDE app**
- **React Native support**: can build React Native mobile apps via Replit Agent
- Web apps are responsive and usable on mobile browsers
- Replit can generate Expo-based React Native projects

### 10. Unique Aspects

- SOC 2 Type II compliance
- Built-in database (PostgreSQL-compatible)
- Git, GitLab, Bitbucket integration
- Cron jobs, bots, scrapers all buildable via Agent
- Most programming language flexibility (50+ languages)
- Older/more established platform than Bolt or Lovable

---

## Part C: Bolt.new + Lovable + v0 + Tempo Labs

### Bolt.new (stackblitz.com)

#### Prompt --> Full App

- AI-powered full-stack web app generation from natural language prompt
- Built on **StackBlitz WebContainers** -- full Node.js runtime in WebAssembly inside browser
  - npm packages install and run client-side
  - No remote VM during development
  - AI agent has complete control: filesystem, terminal, package manager, browser console
  - Considered Bolt.new's core competitive advantage ("moat")
- **Enhance Prompt** button: rewrites rough prompts into structured specs before generation
- Self-healing debug loops: detects and fixes errors automatically
- Live preview during generation
- Open source (GitHub: `stackblitz/bolt.new`, 16K+ stars, MIT license)

#### Full-stack generated

- Frontend + backend generated in browser
- Frontend: React, Next.js, Vue, Svelte, Angular, Astro, Remix, Vite
- Backend: Node.js/Express only (no Python, Go, etc.)
- Built-in databases, authentication, API endpoints
- User auth: sign-up flows, login, password reset, role-based access
- **Unlimited free databases** on all plans

#### Deployment

- One-click deploy to Netlify, Vercel, Cloudflare Pages
- Built-in hosting with custom domains on paid plans (Bolt v2)
- SEO tools: meta tags, Open Graph, sitemaps, SSR (paid plans)

#### Pricing

| Plan | Price | Tokens | Key Limits |
|------|-------|--------|------------|
| **Free** | $0/mo | 1M/mo (300K daily) | Bolt branding, 10MB upload, 333K web requests |
| **Pro** | $25/mo | 10M+/mo | No daily limit, no branding, custom domains, 100MB upload, 1M web requests, token rollover |
| **Pro Scaled** | $50-2,000/mo | 26M-1,200M/mo | Heavy daily users |
| **Teams** | $30/member/mo | Per-member | Admin tools, centralized billing, design systems, private NPM |
| **Enterprise** | Custom | Custom | SSO, audit logs, compliance, 24/7 support |

- Token rollover: unused tokens roll over 1 month
- On-demand packs: $20 for 10M tokens, never expire while subscribed
- Annual billing saves ~10%

#### Tech Stack

- Frontend: React, Next.js, Vue, Svelte, Angular, Astro, Remix, Vite
- Backend: Node.js/Express
- Styling: Tailwind CSS (default), CSS modules
- Multi-model AI: GPT-5.4, Claude Opus 4.7, Gemini 3.1 Pro (auto-routes to best model)
- Figma import, GitHub import

#### Strengths & Weaknesses

- **Pros**: fastest prototyping (4-5 min to live URL), zero setup, broadest framework support, open source
- **Cons**: backend restricted to Node.js, code quality described as "half done" or "disposable," token limits restrictive on complex projects, WebAssembly slower than native, vendor lock-in concerns

### Lovable (lovable.dev)

#### Builder Experience

- **Browser-based** AI app builder -- no IDE required
- **Chat Mode + Agent Mode + Visual Edits**: three interaction modes
- **Visual Edit**: click any UI element, describe the change, AI applies it directly -- modifies actual code
- **Agentic Mode** (2026): autonomous reasoning and execution
- **Design template library** with pre-built components
- **Team workspaces** for collaboration
- $100M ARR in 8 months (fastest ever for AI dev tool)
- Originally "GPT Engineer," rebranded to Lovable

#### Full-stack

- **Next.js + TypeScript + Tailwind CSS** stack
- **Supabase built-in**: PostgreSQL, auth, storage, real-time subscriptions
- **Auth**: Email, Google, GitHub (via Supabase)
- **Stripe payments** integration
- Database-driven apps with real-time capabilities
- File storage via Supabase Storage

#### Agent Capabilities

- Idea --> deployed app with Supabase backend, auth, database, Stripe
- **Chat for iteration**: refine after initial build with context retention
- Can build full SaaS apps: landing page, auth, dashboard, payment flow, database
- Code quality rated highest among browser-based builders (~8/10)
- Clean, idiomatic code; dev-friendly output

#### Pricing

| Plan | Price | Credits |
|------|-------|---------|
| **Free** | $0/mo | 5 messages/day |
| **Launch/Pro** | ~$25/mo | 100 credits |
| **Scale** | ~$50-100/mo | Higher limits |
| **Team** | ~$50/mo | Shared workspace |

- Credit rollover (2026 update)
- Credits burn on AI generations; hallucinations waste credits

#### Strengths & Weaknesses

- **Pros**: fastest idea-to-deployed-SaaS, best for non-technical founders, cleanest code, native Supabase + Stripe, two-way GitHub sync, Visual Edits
- **Cons**: Supabase dependency (hard to switch), React/TS/Tailwind only, no Vue/Svelte, web-only, credit limits on free tier

### v0 by Vercel

#### UI Generation

- Originally `v0.dev`, rebranded to **v0.app** (August 2025)
- Best-in-class UI quality using **shadcn/ui + Tailwind CSS + React/Next.js**
- Generates production-ready TypeScript/React components
- Copy-paste components into your project
- Supports: Material UI, react-three-fiber (3D), framer-motion, react-flow
- Custom fonts via Google Fonts, color palettes, CSS adjustments
- Multi-modal: screenshots, wireframes, Figma files --> real interfaces
- **Figma-to-code** and **image-to-code** capabilities

#### v0 Agent (2026)

- Evolved from UI generator to **fully agentic development platform**
- **Web Search**: real-time web search with citations and rich result display
- **Site Inspection**: visits and analyzes any website, captures screenshots, extracts content
- **Automatic Error Fixing**: detects missing files, syntax issues, runtime errors, import/export problems
- **"Fix with v0" button**: sends deployment error logs to v0 for auto-fix (up to 20 free uses/day)
- **The Composite Pipeline** (Jan 2026) -- three core innovations:
  1. **Dynamic System Prompt**: detects intent via embeddings, injects up-to-date knowledge (e.g., latest SDK APIs)
  2. **LLM Suspense**: streaming manipulation layer -- fixes issues mid-stream in <100ms (e.g., outdated icon imports, long URLs)
  3. **Autofixers**: combination of deterministic fixes + fine-tuned model for cross-file issues in <250ms
- Real-time previews with visual progress indicators and task cards
- Manual stop at any time; auto-continue for multi-step tasks
- Third-party integrations via Vercel Marketplace (Supabase, databases)

#### v0 Integration with Vercel

- Seamless deploy to Vercel
- Connect to existing Vercel projects
- GitHub repo import, Git panel (branches, PRs)
- AWS database integrations (Aurora, DynamoDB), Snowflake integration
- Sandbox runtime environment

#### Pricing

| Plan | Price | Credits |
|------|-------|---------|
| **Free** | ~$5 credits/mo | Limited |
| **Pro** | $20/mo | 5,000 credits |
| **Team** | $30/user/mo | Shared |
| **Enterprise** | Custom | Custom |

- Credits burn fast on complex iterations; frontend only on free tier

#### Limitations

- Frontend only -- no backend, database, or auth built-in (though Vercel Marketplace integrations exist)
- React/Next.js only
- Credits burn fast on complex iterations

### Tempo Labs (tempolabs.ai / tempo.new)

#### Overview

- Browser-based AI platform: natural language --> full-stack React applications
- Started as visual React editor for designer-developer collaboration
- Evolved into full prompt-to-app platform with multi-agent AI planning
- **React-only** (no Vue, Svelte, Angular)

#### Multi-Agent Planning

- Standout feature: multiple AI agents collaborate on planning BEFORE writing code
- Generates **user flow diagrams, screen breakdowns, architecture outlines** pre-code
- Reduces wasted iterations; catches structural issues early

#### Visual Editor

- **Design-tool-like interface** in browser
- Drag and drop React components, adjust layouts, change styles
- Modifies actual React code underneath (not a separate design layer)
- See results in real-time

#### AI-Powered UI Generation

- UI from text and image prompts
- Create brand styles and typographies from images
- Iterative chat refinement with context retention

#### Code Import/Export

- **GitHub Import**: import existing repos retaining project history
- **Storybook Integration**: import Storybook components
- **Open in VSCode**: edit locally
- **Local Mode**: one-click sync between Tempo and VS Code, Cursor, Windsurf
- **Push to GitHub**: version control maintained
- **Download as Zip**: full codebase export
- No proprietary lock-in: standard React/Next.js code

#### MCP App Store

- Plugin marketplace for integrations
- Stripe (payments), Resend (email), Supabase, Clerk (auth)
- AI agents, messaging
- No manual integration code needed

#### Mobile & Templates

- **React Native** support via **Expo** integration (Expo V2)
- SaaS templates: pre-built components for Stripe, Supabase, Clerk

#### Security & Config

- Environment variables management (secure, not exposed to AI)
- Custom Knowledge: persistent project context for AI (tech stack, design system, preferences)
- Figma Plugin: syncs Figma designs directly with React code

#### Pricing

| Plan | Price | Credits |
|------|-------|---------|
| **Free** | $0/mo | 30 credits (max 5/day), free error fixes |
| **Pro** | $30/mo | 150 credits, full code & reasoning agents |
| **Agent+** | $4,500/mo | 1-3 features/day, human engineers & designers, 48-72hr turnaround, unlimited revisions |
| **Bonus Credits** | $50 for 250 | Never expire |

#### Limitations

- **React-only** -- no other frameworks
- Large price gap between Pro ($30) and Agent+ ($4,500)
- Limited enterprise security documentation (SSO, SOC2 not publicly advertised)
- No native iOS/Android binaries -- web apps + React Native via Expo

### Comparative Summary: Bolt vs Lovable vs v0 vs Tempo vs Replit

| | **v0** | **Bolt.new** | **Lovable** | **Tempo** | **Replit Agent** |
|---|---|---|---|---|---|
| **Best For** | UI components & design | Fastest prototypes | Full-stack SaaS MVPs | Visual React editing + AI | Backend-heavy, multi-language |
| **Stack** | React/Next.js/TS | JS full-stack (multi-framework) | React/TS/Tailwind + Supabase | React/Next.js | 50+ languages |
| **Backend** | No native | Node.js/Express | Supabase (PostgreSQL) | Via MCP plugins | Real cloud VM |
| **Auth** | No | Yes (recent) | Yes (Email, Google, GitHub) | Via Clerk plugin | Yes |
| **Visual Editor** | No | No | Visual Edits (click-to-edit) | Full drag-and-drop | Design Canvas |
| **Multi-Agent Plan** | No | No | No | **Yes** | Plan Mode |
| **Code Quality** | High (UI only) | Moderate | Highest (overall) | Good | Varies by language |
| **Speed to URL** | Quick | ~4-5 min (fastest) | ~8-10 min | Moderate | ~10-20 min |
| **Free Tier** | $5 credits/mo | 1M tokens/mo | 5 messages/day | 30 credits | Daily credits capped |
| **Pro Price** | $20/mo | $25/mo | ~$25/mo | $30/mo | $20-25/mo |
| **Open Source** | No | Yes (MIT) | No | No | No |
| **Mobile** | No | Web only | Web only | React Native/Expo | React Native |
| **Unique Moat** | shadcn/ui + Vercel deploy | WebContainers (in-browser Node.js) | Supabase + Stripe native | Drag-and-drop + multi-agent | 50+ languages + terminal |

---

## Part D: Other Notable Tools

### Droid (factory.ai)

#### What Is It

- Enterprise-grade AI coding agent built by **Factory AI**
- Lives in your **terminal** -- `droid` command
- Handles end-to-end development workflows
- Specification mode: describe features in plain language --> auto plan then implement
- Auto-Run Modes: Low / Medium / High autonomy with Shift+Tab cycling

#### Key Features

| Feature | Detail |
|---------|--------|
| **IDE Integration** | VS Code, Cursor, Windsurf, JetBrains |
| **Custom Models** | BYOK -- Z.AI GLM, OpenAI, Anthropic, etc. |
| **Review Depth** | `deep` (GPT-5.2) or `shallow` (Kimi K2) presets |
| **MCP Support** | Model Context Protocol |
| **Docker Sandbox** | `sbx run droid` for isolated execution |
| **GitHub Action** | `@droid fill` (PR descriptions), `@droid review` (code review), `@droid security` (STRIDE review) |

#### 2026 Updates

- Droid GitHub Action v5 (April 16, 2026): auto PR descriptions, automated code review with inline comments, STRIDE-based security review with `--full` option
- Git AI integration (January 15, 2026): AI attribution on commits
- Docker Sandbox support

#### Pricing

| Plan | Price | Key Features |
|------|-------|-------------|
| **Pro** | $20/mo | Complete dev agents, desktop/CLI/SDK, cloud & local background agents |
| **Plus** | $100/mo | ~5x Pro usage, Droid Computers (managed cloud computers) |
| **Max** | $200/mo | ~10x Pro usage, early access to new features |
| **Teams** | Contact sales | Up to 150 seats, custom limits, SSO, ZDR, admin controls |
| **Enterprise** | Contact sales | Unlimited seats, dedicated compute, on-prem (including air-gapped), audit logs, SOC 2/ISO 27001/42001 |

- **Droid Core**: free fallback tier using open-weight models when limits hit
- **Extra Usage**: prepaid credits, $10 minimum, never expire
- Rate limits: rolling windows (5-hour, weekly, monthly)

#### Enterprise Security

- SOC 2, ISO 27001, ISO 42001 certified
- Defense-in-depth: command risk classification, allow/deny lists, Droid Shield (secret scanning), programmable hooks, sandboxed runtimes
- OTEL-native observability
- Deployment: cloud-managed, hybrid enterprise, or fully air-gapped
- Teams & Enterprise not affected by rate limit changes

#### Install

```bash
curl -fsSL https://app.factory.ai/cli | sh   # macOS/Linux
irm https://app.factory.ai/cli/windows | iex   # Windows
```

### Kiro (kiro.dev)

#### Overview

- AI-powered **agentic coding IDE** by AWS, built on Amazon Bedrock
- **Spec-Driven Development**: structured approach vs "vibe coding"
- Installed via `curl -fsSL https://cli.kiro.dev/install | bash`

#### Spec-Driven Workflow (3 Phases)

| Phase | Output | Description |
|-------|--------|-------------|
| **1. Requirements** | `requirements.md` | Natural language --> structured requirements, user stories, acceptance criteria (EARS notation) |
| **2. Design** | `design.md` | Architecture, system design, tech stack, data flow, pseudocode |
| **3. Tasks** | `tasks.md` | Granular, sequenced implementation tasks with acceptance criteria |

- All specs saved as version-controlled Markdown (`.kiro/specs/`)
- Reviewed, edited, and committed alongside code
- Core philosophy: *"A specification is a kind of version-controlled, human-readable super prompt."*

#### Key Capabilities

- **Codebase Analysis**: before building, generates `structure.md`, `tech.md`, `product.md`
- **Autopilot Mode**: autonomous execution of large tasks
- **Agent Hooks**: auto-trigger agents on events like file-save (e.g., auto-generate tests)
- **Steering Files**: persistent markdown configs for coding standards, conventions, workflows per project
- **Multimodal Input**: images (UI mockups, whiteboard photos) as implementation guides
- **Native MCP Support**: connect to docs, databases, APIs
- **Model Choice**: Claude Sonnet 4.5 and other frontier models

#### Position

- Bridges gap from "vibe coding" to structured, production-ready engineering
- Durable, repeatable collaboration between developer and AI
- Intent is explicit, decisions are documented, agent has a "North Star"

### Google Antigravity

#### Overview

- **Agent-first IDE** by Google DeepMind, launched public preview November 18, 2025
- Fork of VS Code -- extensions, keybindings, themes transfer immediately
- Paradigm shift: AI code suggestions --> autonomous AI agents
- Installed from **antigravity.google**

#### Key Features

| Feature | Detail |
|---------|--------|
| **Agent-First** | Agents autonomously plan --> code --> test --> verify |
| **Manager View** | Mission-control dashboard -- multiple agents in parallel across workspaces |
| **Editor View** | Familiar VS Code experience with tab completions and inline AI chat |
| **Artifacts System** | Agents produce: implementation plans, code diffs, screenshots, browser recordings |
| **Browser Control** | Integrated Chrome -- agents navigate, click, screenshot, verify web UIs |
| **Multi-Model** | Gemini 3.1 Pro, Gemini 3 Flash, Claude Sonnet 4.6, Claude Opus 4.6, GPT-OSS 120B |
| **MCP Support** | Connect agents to external services |
| **AgentKit 2.0** | 16 specialized agents, 40+ domain-specific skills (March 2026) |
| **Context** | 1M token window via Gemini 3.1 Pro |

#### Pricing

| Plan | Price | Details |
|------|-------|---------|
| **Free** | $0/mo | All models, rate-limited (~20 req/day, 5/min on Gemini 3.1 Pro), weekly quota |
| **AI Pro** | ~$20/mo | Higher limits, priority access, bundled with Google One AI Premium |
| **AI Ultra** | $249.99/mo | 20x-ish credits, highest limits, sustained multi-agent workloads |
| **Credit Top-ups** | $25 for 2,500 | Supplemental |

- **Credit opacity**: no published credit-to-token conversion rate
- March 2026: free-tier quota cuts triggered community backlash (nicknamed "paperweight")

#### Pros

- Genuinely free during preview (no credit card)
- Most ambitious agent-first architecture on the market
- Parallel multi-agent execution via Manager View
- Artifacts create transparent audit trail (plans, diffs, recordings)
- Built-in browser control for visual verification (unique)
- VS Code foundation -- extensions work immediately

#### Cons

- **Preview instability** -- "agent terminated" errors during complex operations
- **Rate limit frustrations** -- heavy users hit walls mid-session
- **Over-engineers** simple tasks (CSS fix --> full design system refactor)
- **Pricing controversy** -- free-tier cuts, opaque credits
- **Learning curve** for Manager View
- **No JetBrains** support
- **Slower model adoption** -- still on Opus 4.6 while competitors shipped 4.7
- **Gemini-centric bias** -- Claude/GPT available but Gemini gets priority

#### vs Competitors

| | Antigravity | Cursor | GitHub Copilot | Claude Code |
|---|---|---|---|---|
| **Philosophy** | Agent-first | Editor-first | Plugin-first | Terminal-first |
| **Parallel Agents** | Yes (Manager View) | Cloud Agents | No | No (single agent) |
| **Browser Control** | Native Chrome | No | No | No |
| **Audit Trail** | Full artifacts | Limited | No | Terminal output |
| **Stability** | Preview-grade | Excellent | Excellent | Good |
| **Free Tier** | Yes | Trial only | No | Usage-based |

- **Verdict**: Most ambitious AI IDE -- best for experimentation and greenfield, not yet reliable as daily driver for production.

### Open Interpreter

#### Overview

- **Open source** (AGPL-3.0, 63K+ GitHub stars, 130+ contributors)
- LLMs run code directly on your computer via natural language
- Like ChatGPT Code Interpreter but: no internet restrictions, any package, no size/time limits, persistent local state

#### Key Capabilities

- Create/edit photos, videos, PDFs
- Control Chrome browser for research and scraping
- Plot, clean, analyze large datasets
- File management (create, modify, any file on disk)
- Persistent state: variables, imports, results survive across messages
- Multi-model: OpenAI (GPT-4o, GPT-4), Anthropic (Claude), any OpenAI-compatible endpoint
- Local LLMs via Ollama, LlamaCpp, LM Studio, jan.ai
- Platforms: macOS, Linux, Windows, Android (Termux), GitHub Codespaces

#### "New Computer" Update (Desktop Agent)

- **Mac Control API**: control native macOS apps (`calendar`, `contacts`, `browser`, `mail`, `sms`)
- **Point Model**: local vision model -- locates visual UI controls (icons, buttons) on screen for mouse automation
- **LLM-first Web Browser**: query web-enabled LLM for browsing
- 5x launch speed improvements
- Experimental Docker support

#### Developer Features

- REST API server (FastAPI)
- Streaming responses
- Save/restore conversation history
- Custom system messages
- YAML-based profiles (like Custom GPTs)
- Export to Jupyter notebooks (`%jupyter`)
- Interactive terminal: `%verbose`, `%reset`, `%undo`, `%tokens`, `%help`

#### Safety

- Code approval before execution (can bypass with `-y` or `auto_run: True`)
- Experimental safe mode
- Docker isolation option

#### Current Status

- Latest tagged release: v0.4.2 (prerelease, October 2024)
- Actively developed into 2026 (last GitHub push Feb 9, 2026)
- Desktop app: early access at openinterpreter.com

---

## Part E: CLI Coding Agents -- Head-to-Head Comparison

### Claude Code (Anthropic) vs Codex CLI (OpenAI) vs Gemini CLI (Google)

#### Quick Comparison

| Dimension | **Claude Code** | **Codex CLI** | **Gemini CLI** |
|---|---|---|---|
| **License** | Source-available | Apache 2.0 | Apache 2.0 |
| **Free Tier** | None | Included with ChatGPT Plus | 1,000 req/day (Flash-only) |
| **Entry Price** | $20/mo (Pro) or API | $20/mo (Plus) or API | Free |
| **Mid/Heavy Tier** | $100-200/mo (Max) | $100-200/mo (Pro, Apr 2026) | Pay-as-you-go for Pro models |
| **Default Model** | Opus 4.7 / Sonnet 4.6 | GPT-5.3-Codex-Spark | Gemini 3 Flash (free) / 3.1 Pro (paid) |
| **Context Window** | 1M tokens | Up to 1M (GPT-5.4) | 1M tokens |
| **Model Flexibility** | Claude only | OpenAI only | Gemini only |
| **Open Source** | No | Yes | Yes |

#### Code Quality

- **Claude Code**: consensus leader for complex architecture, multi-file refactoring, first-try correctness. Scored 78% usable without human edits (Morph benchmark). 9/10 on REST API, debugging, refactoring.
- **Codex CLI**: strong on DevOps, infrastructure, token efficiency. Weaker on frontend. Codex Desktop (Apr 2026) adding `gpt-image-1.5` to address this.
- **Gemini CLI**: inconsistent -- "either great or garbage, coin toss." Gemini 3.1 Pro improving.

#### Autonomy & Security

- **Claude Code**: step-wise approval (diffs before applying), Agent Teams for multi-agent, `--permission-mode` controls. Locked-down: banned third-party harnesses from subscription coverage (Apr 2026).
- **Codex CLI**: OS-level sandbox (Seatbelt/Landlock + seccomp). Runs in isolation, you inspect, decide to apply. Local models via `--oss` (Ollama/LM Studio). Largest ecosystem: 9,000+ MCP servers + 90 proprietary plugins.
- **Gemini CLI**: autonomous-by-default (YOLO with `-y`). Plan Mode for safety. Docker sandboxing. Extensions/Skills/Hooks system. Google Workspace integration. ACP support for editor integration.

#### Cost & Token Economics

- **Gemini CLI** wins on price: free tier 1,000 req/day
- **Codex CLI** most token-efficient: 2-3x fewer tokens than Claude Code on same task, $3-8/day on API
- **Claude Code** most expensive: $10-30/day on API. Opus 4.7 tokenizer encodes up to 35% more tokens than 4.6, raising effective cost

#### Real-world Benchmarks (Render, 7 dimensions)

| Task | Claude Code | Codex CLI | Gemini CLI |
|------|-------------|-----------|------------|
| Build REST API | 3 min, 9/10 | -- | -- |
| Debug test suite | 5 min, 9/10 | -- | -- |
| Refactor legacy | 10 min, 9/10 | -- | -- |
| Full SaaS from spec | 50 min, 85% | -- | -- |
| **Overall score** | **6.8/10** | **6.0/10** | **6.8/10** |

#### Power-User Patterns

- Claude Code for architecture + Codex CLI for debugging/DevOps
- Claude Code for production + Gemini CLI (free) for prototyping
- Aider for cost-controlled iteration + Claude Code for complex planning

---

## Part F: Meta-Analysis -- Category Taxonomy

### How These Tools Break Down

#### Category 1: Fully Autonomous Cloud Agents
- Deploy to cloud, work independently, return PRs, operate while you sleep
- **Devin**, **Managed Devins**, **Replit Agent 3 (Max Autonomy)**

#### Category 2: Agentic IDEs
- Forked or native IDEs with deep AI agent integration, multi-model, browser control
- **Antigravity** (Google), **Cursor**, **Windsurf**, **Kiro** (AWS), **Factory IDE**

#### Category 3: Browser-Based "Prompt-to-App" Builders
- Zero-setup, natural language --> full app in browser, one-click deploy
- **Bolt.new**, **Lovable**, **v0**, **Tempo**, **Replit Agent**

#### Category 4: Terminal / CLI Agents
- Live in terminal, pair-programming model, deep codebase understanding
- **Claude Code**, **Codex CLI**, **Gemini CLI**, **Droid**, **Open Interpreter**, **Aider**

#### Category 5: Specialized / Niche
- **v0**: UI generation specialist evolved into agent
- **Tempo**: visual React editing + multi-agent planning
- **Kiro**: spec-driven engineering rigor
- **Open Interpreter**: general computer control, not just coding
- **Devin Terminal**: CLI bridge to cloud Devin

### Key Market Trends (2026)

1. **"Vibe coding"** (Karpathy, early 2025) --> mainstream paradigm by 2026: describe in English, AI builds
2. **Multi-agent orchestration** is the new differentiator: Antigravity Manager View, Devin Managed Devins, Claude Code Agent Teams
3. **Hybrid workflows** dominant: pros use v0 for UI, Lovable for backend/auth, Cursor for daily coding, Claude Code for heavy refactoring
4. **Browser control** emerging as key capability: Devin 2.2 desktop apps, Antigravity Chrome, Replit Agent 3 browser testing
5. **Pricing fragmentation**: from free (Gemini CLI) to $500/mo+ (Devin Teams) to $4,500/mo (Tempo Agent+)
6. **"Last 10-30%" problem** is universal: no tool fully replaces human judgment on architecture, security, edge cases
7. **Security concerns**: AI-generated code has ~1.7x more "major" issues and ~2.74x more security vulnerabilities than human code (CodeRabbit)
8. **Consolidation**: Cognition bought Windsurf; Google entered with Antigravity; AWS with Kiro
9. **Open source vs closed**: Codex CLI and Gemini CLI are Apache 2.0; Bolt.new is MIT; Claude Code and Devin are closed
10. **Terminal era**: serious developers running multiple CLI agents in parallel, worktree isolation, background agents

### Which Tool for Which Job?

| Scenario | Best Tool |
|---|---|
| Well-defined bug with repro steps | Devin |
| Test coverage gap filling | Devin or Droid |
| Code migration / modernization | Devin (8-12x speedup) |
| UI component / landing page | v0 |
| Rapid prototype / hackathon demo | Bolt.new |
| Full SaaS MVP with payments | Lovable |
| Complex backend service / Python | Replit Agent |
| Visual React development | Tempo |
| Complex architecture / refactoring | Claude Code |
| Daily coding / inline AI | Cursor |
| DevOps / infrastructure | Codex CLI |
| Budget / free tier exploration | Gemini CLI |
| Enterprise compliance / air-gap | Factory Droid Enterprise |
| Spec-driven structured development | Kiro |
| Computer automation beyond coding | Open Interpreter |
| Multi-agent parallel experimentation | Antigravity (Manager View) |
| Slack-native task assignment | Devin (via Slack @Devin) |
| Education / learning to code | Replit |
| Open source / self-hosted | Codex CLI, Gemini CLI, Open Interpreter |

---

### Sources

- [Devin Official Site](https://devin.ai/)
- [Devin Docs -- Introduction](https://docs.devin.ai/get-started/devin-intro)
- [Cognition -- Devin 2.2](https://cognition.ai/blog/introducing-devin-2-2)
- [Devin Docs -- Advanced Capabilities](https://docs.devin.ai/work-with-devin/advanced-capabilities)
- [Devin Review 2026 -- Agent Finder](https://agent-finder.co/reviews/devin)
- [Devin Review 2026 -- Idlen](https://www.idlen.io/blog/devin-ai-engineer-review-limits-2026/)
- [Honest Devin Review Spring 2026 -- Plain AI](https://plainai.tech/articles/honest-devin-review-spring-2026-pricing-pros-cons-alternatives)
- [Devin Slack Integration Docs](https://docs.devin.ai/integrations/slack)
- [Devin Enterprise Docs](https://docs.devin.ai/enterprise/getting-started/get-started)
- [Replit Pricing](https://replit.com/pricing)
- [Replit -- Agent 3](https://blog.replit.com/introducing-agent-3-our-most-autonomous-agent-yet)
- [Bolt.new Pricing](https://bolt.new/pricing)
- [Bolt.new GitHub](https://github.com/stackblitz/bolt.new/)
- [Bolt.new Complete Guide 2026](https://capacity.so/blog/what-is-bolt-new)
- [Lovable vs Bolt vs v0 2026 -- AppStackBuilder](https://appstackbuilder.com/blog/lovable-vs-bolt-vs-v0-2026)
- [v0 vs Bolt vs Lovable Pricing -- StackCompare](https://stackcompare.net/v0-vs-bolt-new-vs-lovable-2026-ai-app-builder-pricing-compared/)
- [Lovable vs Bolt vs v0 -- Y Build](https://ybuild.ai/en/blog/lovable-vs-bolt-vs-v0-ai-app-builder-comparison-2026)
- [v0 Agentic Features Docs](https://v0.app/docs/agentic-features)
- [How v0 Became a Coding Agent -- Vercel](https://vercel.com/blog/how-we-made-v0-an-effective-coding-agent)
- [Tempo Labs Official Site](https://www.tempolabs.ai/)
- [Tempo Review 2026](https://vibecoding.app/blog/tempo-review)
- [Factory Droid GitHub Action](https://github.com/Factory-AI/droid-action)
- [Factory Pricing](https://factory.ai/pricing)
- [Factory Enterprise Docs](https://docs.factory.ai/enterprise)
- [Code Droid Technical Report](https://factory.ai/news/code-droid-technical-report)
- [Kiro Official Site](https://kiro.dev/)
- [Kiro -- From Chat to Specs](https://kiro.dev/blog/from-chat-to-specs-deep-dive)
- [Open Interpreter Docs](https://docs.openinterpreter.com/)
- [Open Interpreter GitHub](https://github.com/OpenInterpreter/open-interpreter)
- [Google Antigravity Review 2026](https://ohaiknow.com/reviews/google-antigravity/)
- [Antigravity Features & Pricing](https://aipedia.wiki/tools/antigravity/)
- [Claude Code vs Cursor vs Devin vs Windsurf 2026](https://cowork.ink/blog/claude-code-vs-cursor-vs-devin-vs-windsurf/)
- [CLI Coding Agents Compared 2026](https://codemyspec.com/pages/cli-agents-compared-2026)
- [AI Coding Assistants Compared 2026](https://www.paperclipped.de/en/blog/ai-coding-assistants-compared-2026/)
- [Terminal AI Agents 2026](https://gocodelab.com/en/blog/en-codex-cli-vs-claude-code-vs-gemini-cli-terminal-agent-comparison-2026)
- [Best Vibe Coding Tools 2026](https://launchrocket.io/blog/best-vibe-coding-tools)
- [Lovable vs Google Antigravity 2026](https://vibecoding.app/blog/lovable-vs-google-antigravity)
- [AI Coding Tools 2026 Comparison](https://amitray.com/ai-coding-tools-2026-comparison/)
- [Text-to-App Showdown: Replit vs Lovable vs Bolt](https://aiscopelab.com/replit-vs-lovable-vs-bolt/)
- [Tessellate Labs: Lovable vs Bolt vs Replit 2026](https://tessellatelabs.com/knowledge/lovable-vs-bolt-vs-replit-2026)
- [Bolt vs Lovable vs Replit 2026 -- Vibecoding](https://vibecoding.app/blog/bolt-vs-lovable-vs-replit)
