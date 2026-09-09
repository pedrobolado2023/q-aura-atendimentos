// Q-aura SaaS - Superadmin Panel Application Logic

class SuperadminRouter {
    constructor() {
        this.token = localStorage.getItem("sa_token") || localStorage.getItem("qa_token") || null;
        this.apiUrl = window.location.origin;
        this.plans = [];
        this.tenants = [];
    }

    async init() {
        // Bind forms
        document.getElementById("login-form").addEventListener("submit", (e) => this.handleLogin(e));
        document.getElementById("tenant-form").addEventListener("submit", (e) => this.handleSaveTenant(e));
        document.getElementById("plan-form").addEventListener("submit", (e) => this.handleSavePlan(e));

        if (this.token) {
            const ok = await this.verifySession();
            if (ok) {
                this.showLayout();
                this.loadDashboard();
            } else {
                this.logout();
            }
        } else {
            this.showLogin();
        }
    }

    // Toast alerts helper
    showToast(message, type = "success") {
        const container = document.getElementById("toast-container");
        const toast = document.createElement("div");
        toast.className = `toast ${type}`;
        
        let icon = "fa-circle-check";
        if (type === "error") icon = "fa-circle-xmark";
        if (type === "warning") icon = "fa-triangle-exclamation";

        toast.innerHTML = `
            <i class="fa-solid ${icon}"></i>
            <span class="toast-message">${message}</span>
        `;
        
        container.appendChild(toast);
        
        // Remove toast after animation finishes
        setTimeout(() => {
            toast.style.animation = "slideOut 0.3s ease forwards";
            setTimeout(() => toast.remove(), 300);
        }, 3000);
    }

    // API Helper
    async request(endpoint, method = "GET", body = null) {
        const headers = {
            "Content-Type": "application/json"
        };
        if (this.token) {
            headers["Authorization"] = `Bearer ${this.token}`;
        }
        
        const options = { method, headers };
        if (body) {
            options.body = JSON.stringify(body);
        }

        try {
            const response = await fetch(`${this.apiUrl}${endpoint}`, options);
            if (response.status === 401 || response.status === 403) {
                const data = await response.json();
                let detailMsg = data.detail || "Login ou senha incorretos, tente novamente.";
                if (detailMsg.includes("Invalid Credentials") || detailMsg.includes("Invalid credentials")) {
                    detailMsg = "Login ou senha incorretos, tente novamente.";
                }
                this.showToast(detailMsg, "error");
                if (endpoint !== "/api/auth/login") {
                    this.logout();
                }
                return null;
            }
            if (!response.ok) {
                const data = await response.json();
                throw new Error(data.detail || "Erro na requisição");
            }
            return await response.json();
        } catch (error) {
            this.showToast(error.message, "error");
            return null;
        }
    }

    // Login logic
    async handleLogin(e) {
        e.preventDefault();
        const email = document.getElementById("login-email").value;
        const password = document.getElementById("login-password").value;

        const data = await this.request("/api/auth/login", "POST", { email, password });
        if (data) {
            this.token = data.access_token;
            // Verify if superadmin role is returned
            const user = await this.request("/api/auth/me");
            if (user && user.role === "superadmin") {
                localStorage.setItem("sa_token", this.token);
                localStorage.setItem("qa_token", this.token);
                localStorage.setItem("qa_user", JSON.stringify(user));
                this.showToast("Login efetuado com sucesso!");
                this.showLayout();
                this.loadDashboard();
            } else {
                this.showToast("Este painel é reservado exclusivamente para o Superadmin.", "error");
                this.token = null;
            }
        }
    }

    async verifySession() {
        const user = await this.request("/api/auth/me");
        return user && user.role === "superadmin";
    }

    showLogin() {
        document.getElementById("login-view").classList.add("active");
        document.getElementById("main-layout").classList.add("layout-hidden");
    }

    showLayout() {
        document.getElementById("login-view").classList.remove("active");
        document.getElementById("main-layout").classList.remove("layout-hidden");
    }

    logout() {
        localStorage.clear();
        this.token = null;
        this.showLogin();
    }

    // View navigation
    selectTab(e) {
        e.preventDefault();
        const target = e.currentTarget.dataset.target;
        
        // Update menu visual classes
        document.querySelectorAll(".sidebar-menu .menu-item").forEach(item => {
            item.classList.remove("active");
        });
        e.currentTarget.classList.add("active");

        // Toggle workspaces
        document.querySelectorAll(".workspace-view").forEach(view => {
            view.classList.remove("active");
        });
        document.getElementById(target).classList.add("active");

        // Load tab specific data
        if (target === "dashboard-view") this.loadDashboard();
        if (target === "tenants-view") this.loadTenants();
        if (target === "plans-view") this.loadPlans();
        if (target === "pricing-view") this.loadPricing();
    }

    // Dashboard loader
    async loadDashboard() {
        const data = await this.request("/api/superadmin/dashboard");
        if (data) {
            document.getElementById("stat-total-tenants").innerText = data.total_tenants;
            document.getElementById("stat-active-tenants").innerText = data.active_tenants;
            document.getElementById("stat-suspended-tenants").innerText = data.suspended_tenants;
            document.getElementById("stat-total-users").innerText = data.total_users;
            document.getElementById("stat-trial-tenants").innerText = data.trial_tenants;
            document.getElementById("stat-total-plans").innerText = data.total_plans;
        }
    }

    // Tenants management
    async loadTenants() {
        const tableBody = document.getElementById("tenants-table-body");
        tableBody.innerHTML = `<tr><td colspan="7" style="padding:32px; text-align:center;"><i class="fa-solid fa-spinner fa-spin"></i> Carregando...</td></tr>`;

        // Fetch plans first to populate plan selections
        await this.fetchPlansList();

        const data = await this.request("/api/superadmin/tenants");
        if (data) {
            this.tenants = data;
            if (data.length === 0) {
                tableBody.innerHTML = `<tr><td colspan="8" style="padding:32px; text-align:center; color:#64748b;">Nenhuma empresa cadastrada.</td></tr>`;
                return;
            }

            tableBody.innerHTML = "";
            data.forEach(tenant => {
                const tr = document.createElement("tr");
                tr.style.borderBottom = "1px solid #e2e8f0";
                
                let statusBadge = `<span class="badge badge-active">Ativa</span>`;
                if (tenant.status === "suspended") statusBadge = `<span class="badge badge-suspended">Suspensa</span>`;
                if (tenant.status === "trial") statusBadge = `<span class="badge badge-trial">Trial</span>`;

                const date = new Date(tenant.created_at).toLocaleDateString("pt-BR");
                const planName = tenant.plan ? tenant.plan.name : "Customizado / Sem Plano";

                const billingModeText = tenant.billing_mode === "postpaid" ? "Pós-pago" : "Pré-pago";
                const balanceVal = tenant.balance !== undefined ? tenant.balance.toFixed(2) : "0.00";
                const limitVal = tenant.postpaid_limit !== undefined ? tenant.postpaid_limit.toFixed(2) : "100.00";

                tr.innerHTML = `
                    <td style="padding: 16px 24px; font-weight: 500; color: #0f172a;">${tenant.name}</td>
                    <td style="padding: 16px 24px; color: #64748b;">${tenant.subdomain}</td>
                    <td style="padding: 16px 24px;"><span class="badge badge-purple">${planName}</span></td>
                    <td style="padding: 16px 24px; color: #0f172a; font-weight: 600;">${billingModeText}</td>
                    <td style="padding: 16px 24px; color: #10b981; font-weight: 600;">R$ ${balanceVal}</td>
                    <td style="padding: 16px 24px; color: #64748b;">R$ ${limitVal}</td>
                    <td style="padding: 16px 24px;">${statusBadge}</td>
                    <td style="padding: 16px 24px;" class="actions-cell">
                        <button class="btn btn-primary btn-xs" data-action="contract" data-id="${tenant.id}" title="Gerar Contrato Pré-preenchido"><i class="fa-solid fa-file-contract"></i></button>
                        <button class="btn btn-success btn-xs" data-action="billing" data-id="${tenant.id}" title="Gerenciar Faturamento"><i class="fa-solid fa-credit-card"></i></button>
                        <button class="btn btn-secondary btn-xs" data-action="edit" data-id="${tenant.id}"><i class="fa-solid fa-pen"></i></button>
                        <button class="btn btn-danger btn-xs" data-action="delete" data-id="${tenant.id}"><i class="fa-solid fa-trash"></i></button>
                    </td>
                `;
                tableBody.appendChild(tr);
            });

            // Attach dynamic listeners for action buttons
            tableBody.querySelectorAll("button[data-action='contract']").forEach(btn => {
                btn.addEventListener("click", () => this.openContractModal(btn.dataset.id));
            });
            tableBody.querySelectorAll("button[data-action='billing']").forEach(btn => {
                btn.addEventListener("click", () => this.openBillingModal(btn.dataset.id));
            });
            tableBody.querySelectorAll("button[data-action='edit']").forEach(btn => {
                btn.addEventListener("click", () => this.openEditTenantModal(btn.dataset.id));
            });
            tableBody.querySelectorAll("button[data-action='delete']").forEach(btn => {
                btn.addEventListener("click", () => this.handleDeleteTenant(btn.dataset.id));
            });
        }
    }

    async fetchPlansList() {
        const data = await this.request("/api/superadmin/plans");
        if (data) {
            this.plans = data;
            const select = document.getElementById("tenant-form-plan");
            select.innerHTML = `<option value="">Selecione um plano...</option>`;
            data.forEach(plan => {
                select.innerHTML += `<option value="${plan.id}">${plan.name} (R$ ${plan.price_monthly})</option>`;
            });
        }
    }

    openNewTenantModal() {
        document.getElementById("tenant-modal-title").innerText = "Cadastrar Nova Empresa";
        document.getElementById("tenant-modal-id").value = "";
        document.getElementById("tenant-form").reset();
        
        // Show fields for new admin user
        document.getElementById("tenant-form-admin-section").style.display = "block";
        document.getElementById("tenant-form-admin-name").required = true;
        document.getElementById("tenant-form-admin-email").required = true;
        document.getElementById("tenant-form-admin-password").required = true;
        
        // Hide status field for new tenants
        document.getElementById("tenant-form-status-group").style.display = "none";
        
        document.getElementById("tenant-modal").classList.add("active");
    }

    openEditTenantModal(id) {
        const tenant = this.tenants.find(t => t.id === id);
        if (!tenant) return;

        document.getElementById("tenant-modal-title").innerText = "Editar Empresa";
        document.getElementById("tenant-modal-id").value = tenant.id;
        document.getElementById("tenant-form-name").value = tenant.name;
        document.getElementById("tenant-form-subdomain").value = tenant.subdomain;
        document.getElementById("tenant-form-cnpj").value = tenant.cnpj || "";
        document.getElementById("tenant-form-segment").value = tenant.segment || "hotel";
        document.getElementById("tenant-form-plan").value = tenant.plan ? tenant.plan.id : "";
        document.getElementById("tenant-form-max-users").value = tenant.max_users || 5;
        document.getElementById("tenant-form-status").value = tenant.status;

        // Hide admin creation fields during edits
        document.getElementById("tenant-form-admin-section").style.display = "none";
        document.getElementById("tenant-form-admin-name").required = false;
        document.getElementById("tenant-form-admin-email").required = false;
        document.getElementById("tenant-form-admin-password").required = false;

        // Show status field
        document.getElementById("tenant-form-status-group").style.display = "block";

        // Check custom modules
        const customModules = tenant.custom_modules || [];
        document.querySelectorAll("input[name='custom_modules']").forEach(cb => {
            cb.checked = customModules.includes(cb.value);
        });

        document.getElementById("tenant-modal").classList.add("active");
    }

    closeTenantModal() {
        document.getElementById("tenant-modal").classList.remove("active");
    }

    async handleSaveTenant(e) {
        e.preventDefault();
        const id = document.getElementById("tenant-modal-id").value;
        
        // Collect custom modules checkboxes
        const custom_modules = [];
        document.querySelectorAll("input[name='custom_modules']:checked").forEach(cb => {
            custom_modules.push(cb.value);
        });

        const tenantData = {
            name: document.getElementById("tenant-form-name").value,
            cnpj: document.getElementById("tenant-form-cnpj").value,
            segment: document.getElementById("tenant-form-segment").value,
            plan_id: document.getElementById("tenant-form-plan").value || null,
            max_users: parseInt(document.getElementById("tenant-form-max-users").value) || 5
        };

        if (id) {
            // Edit mode
            tenantData.status = document.getElementById("tenant-form-status").value;
            tenantData.custom_modules = custom_modules;

            const res = await this.request(`/api/superadmin/tenants/${id}`, "PUT", tenantData);
            if (res) {
                this.showToast("Empresa atualizada com sucesso!");
                this.closeTenantModal();
                this.loadTenants();
            }
        } else {
            // Creation mode
            tenantData.subdomain = document.getElementById("tenant-form-subdomain").value;
            tenantData.admin_name = document.getElementById("tenant-form-admin-name").value;
            tenantData.admin_email = document.getElementById("tenant-form-admin-email").value;
            tenantData.admin_password = document.getElementById("tenant-form-admin-password").value;

            const res = await this.request("/api/superadmin/tenants", "POST", tenantData);
            if (res) {
                this.showToast("Empresa e Administrador cadastrados com sucesso!");
                this.closeTenantModal();
                this.loadTenants();
            }
        }
    }

    async handleDeleteTenant(id) {
        if (confirm("Tem certeza absoluta de que deseja excluir esta empresa? Todos os contatos, conversas e credenciais serão apagados permanentemente.")) {
            const res = await this.request(`/api/superadmin/tenants/${id}`, "DELETE");
            if (res) {
                this.showToast("Empresa excluída com sucesso!");
                this.loadTenants();
            }
        }
    }

    // Plans management
    async loadPlans() {
        const tableBody = document.getElementById("plans-table-body");
        tableBody.innerHTML = `<tr><td colspan="6" style="padding:32px; text-align:center;"><i class="fa-solid fa-spinner fa-spin"></i> Carregando...</td></tr>`;

        const data = await this.request("/api/superadmin/plans");
        if (data) {
            this.plans = data;
            if (data.length === 0) {
                tableBody.innerHTML = `<tr><td colspan="6" style="padding:32px; text-align:center; color:#64748b;">Nenhum plano cadastrado.</td></tr>`;
                return;
            }

            tableBody.innerHTML = "";
            data.forEach(plan => {
                const tr = document.createElement("tr");
                tr.style.borderBottom = "1px solid #e2e8f0";

                const price = parseFloat(plan.price_monthly).toLocaleString("pt-BR", { style: "currency", currency: "BRL" });
                const modulesList = (plan.modules || []).map(mod => {
                    const dict = {
                        inbox: "Inbox",
                        chatbot: "Chatbot",
                        crm: "CRM/Marketing",
                        team: "Equipe",
                        dashboard: "Métricas",
                        meta_settings: "Meta Config"
                    };
                    return dict[mod] || mod;
                }).join(", ");

                tr.innerHTML = `
                    <td style="padding: 16px 24px; font-weight: 500; color: #0f172a;">${plan.name}</td>
                    <td style="padding: 16px 24px; color: #4f46e5; font-weight: 600;">${price}</td>
                    <td style="padding: 16px 24px; color: #64748b;">${plan.max_users} usuários</td>
                    <td style="padding: 16px 24px; color: #475569; font-size: 0.8125rem;">${modulesList || "Nenhum"}</td>
                    <td style="padding: 16px 24px;"><span class="badge ${plan.is_active ? 'badge-active' : 'badge-suspended'}">${plan.is_active ? 'Ativo' : 'Inativo'}</span></td>
                    <td style="padding: 16px 24px;" class="actions-cell">
                        <button class="btn btn-secondary btn-xs" data-action="edit-plan" data-id="${plan.id}"><i class="fa-solid fa-pen"></i></button>
                        <button class="btn btn-danger btn-xs" data-action="delete-plan" data-id="${plan.id}"><i class="fa-solid fa-trash"></i></button>
                    </td>
                `;
                tableBody.appendChild(tr);
            });

            // Attach dynamic listeners for action buttons
            tableBody.querySelectorAll("button[data-action='edit-plan']").forEach(btn => {
                btn.addEventListener("click", () => this.openEditPlanModal(btn.dataset.id));
            });
            tableBody.querySelectorAll("button[data-action='delete-plan']").forEach(btn => {
                btn.addEventListener("click", () => this.handleDeletePlan(btn.dataset.id));
            });
        }
    }

    openNewPlanModal() {
        document.getElementById("plan-modal-title").innerText = "Cadastrar Novo Plano";
        document.getElementById("plan-modal-id").value = "";
        document.getElementById("plan-form").reset();
        document.getElementById("plan-modal").classList.add("active");
    }

    openEditPlanModal(id) {
        const plan = this.plans.find(p => p.id === id);
        if (!plan) return;

        document.getElementById("plan-modal-title").innerText = "Editar Plano";
        document.getElementById("plan-modal-id").value = plan.id;
        document.getElementById("plan-form-name").value = plan.name;
        document.getElementById("plan-form-desc").value = plan.description || "";
        document.getElementById("plan-form-price").value = plan.price_monthly;
        document.getElementById("plan-form-users").value = plan.max_users || 5;

        // Check modules
        const modules = plan.modules || [];
        document.querySelectorAll("input[name='plan_modules']").forEach(cb => {
            cb.checked = modules.includes(cb.value);
        });

        document.getElementById("plan-modal").classList.add("active");
    }

    closePlanModal() {
        document.getElementById("plan-modal").classList.remove("active");
    }

    async handleSavePlan(e) {
        e.preventDefault();
        const id = document.getElementById("plan-modal-id").value;

        // Collect plan modules
        const modules = [];
        document.querySelectorAll("input[name='plan_modules']:checked").forEach(cb => {
            modules.push(cb.value);
        });

        const planData = {
            name: document.getElementById("plan-form-name").value,
            description: document.getElementById("plan-form-desc").value,
            price_monthly: parseFloat(document.getElementById("plan-form-price").value) || 0,
            max_users: parseInt(document.getElementById("plan-form-users").value) || 5,
            modules: modules,
            is_active: true
        };

        if (id) {
            const res = await this.request(`/api/superadmin/plans/${id}`, "PUT", planData);
            if (res) {
                this.showToast("Plano atualizado com sucesso!");
                this.closePlanModal();
                this.loadPlans();
            }
        } else {
            const res = await this.request("/api/superadmin/plans", "POST", planData);
            if (res) {
                this.showToast("Plano cadastrado com sucesso!");
                this.closePlanModal();
                this.loadPlans();
            }
        }
    }

    async handleDeletePlan(id) {
        if (confirm("Tem certeza de que deseja excluir este plano?")) {
            const res = await this.request(`/api/superadmin/plans/${id}`, "DELETE");
            if (res) {
                this.showToast("Plano excluído com sucesso!");
                this.loadPlans();
            }
        }
    }

    // Modal de Faturamento do Superadmin
    openBillingModal(tenantId) {
        const tenant = this.tenants.find(t => t.id === tenantId);
        if (!tenant) return;

        this.activeBillingTenantId = tenantId;
        document.getElementById("billing-modal-tenant-name").innerText = tenant.name;
        document.getElementById("tenant-billing-mode-select").value = tenant.billing_mode || "prepaid";
        document.getElementById("tenant-billing-balance-input").value = "";
        document.getElementById("tenant-billing-limit-input").value = tenant.postpaid_limit || 100.00;

        document.getElementById("tenant-billing-modal").style.display = "flex";
    }

    closeBillingModal() {
        document.getElementById("tenant-billing-modal").style.display = "none";
        this.activeBillingTenantId = null;
    }

    async saveBillingMode() {
        if (!this.activeBillingTenantId) return;
        const billing_mode = document.getElementById("tenant-billing-mode-select").value;

        const res = await this.request(`/api/superadmin/tenants/${this.activeBillingTenantId}/set-billing-mode`, "POST", { billing_mode });
        if (res) {
            this.showToast("Método de cobrança atualizado com sucesso!");
            this.closeBillingModal();
            this.loadTenants();
        }
    }

    async saveTenantBalance() {
        if (!this.activeBillingTenantId) return;
        const amount = parseFloat(document.getElementById("tenant-billing-balance-input").value) || 0.0;

        if (amount <= 0) {
            this.showToast("Insira um valor maior que zero para injetar créditos.", "error");
            return;
        }

        const res = await this.request(`/api/superadmin/tenants/${this.activeBillingTenantId}/add-balance`, "POST", { amount });
        if (res) {
            this.showToast("Crédito adicionado com sucesso!");
            this.closeBillingModal();
            this.loadTenants();
        }
    }

    async saveTenantLimit() {
        if (!this.activeBillingTenantId) return;
        const limit = parseFloat(document.getElementById("tenant-billing-limit-input").value) || 0.0;

        if (limit < 0) {
            this.showToast("Insira um limite válido.", "error");
            return;
        }

        const res = await this.request(`/api/superadmin/tenants/${this.activeBillingTenantId}/set-limit`, "POST", { limit });
        if (res) {
            this.showToast("Limite pós-pago atualizado com sucesso!");
            this.closeBillingModal();
            this.loadTenants();
        }
    }

    // ─── PRICING MANAGEMENT ──────────────────────────────────────────
    async loadPricing() {
        const loadingEl = document.getElementById("pricing-loading");
        const tableEl = document.getElementById("pricing-table");
        const tbodyEl = document.getElementById("pricing-tbody");
        const summaryEl = document.getElementById("pricing-summary");
        const summaryContentEl = document.getElementById("pricing-summary-content");

        if (loadingEl) loadingEl.style.display = "block";
        if (tableEl) tableEl.style.display = "none";
        if (summaryEl) summaryEl.style.display = "none";

        const items = await this.request("/api/superadmin/pricing");
        if (!items) return;

        tbodyEl.innerHTML = "";
        summaryContentEl.innerHTML = "";

        items.forEach(item => {
            const tr = document.createElement("tr");
            tr.style.borderBottom = "1px solid var(--border-color)";
            tr.dataset.category = item.category;

            tr.innerHTML = `
                <td style="padding: 16px; font-weight: 600; font-size: 14px;">
                    <div>${item.label}</div>
                    <span style="font-size: 11px; color: var(--text-muted); font-weight: normal;">ID: ${item.category}</span>
                </td>
                <td style="padding: 16px; text-align: center;">
                    <div style="display: flex; align-items: center; justify-content: center; gap: 4px;">
                        <span style="font-size: 13px; color: var(--text-muted);">R$</span>
                        <input type="number" step="0.01" min="0" value="${item.cost_meta.toFixed(2)}" class="pricing-cost-meta" 
                            style="width: 100px; padding: 6px 10px; border: 1px solid var(--border-color); border-radius: 6px; text-align: right; font-size: 13px; font-weight: 600; background: var(--bg-tertiary);"
                            oninput="superadminRouter.recalculateRow(this)">
                    </div>
                </td>
                <td style="padding: 16px; text-align: center;">
                    <div style="display: flex; align-items: center; justify-content: center; gap: 4px;">
                        <span style="font-size: 13px; font-weight: 700; color: var(--color-brand);">R$</span>
                        <input type="number" step="0.01" min="0" value="${item.price_tenant.toFixed(2)}" class="pricing-price-tenant" 
                            style="width: 100px; padding: 6px 10px; border: 1.5px solid var(--color-brand); border-radius: 6px; text-align: right; font-size: 14px; font-weight: 700; color: var(--color-brand); background: #fff;"
                            oninput="superadminRouter.recalculateRow(this)">
                    </div>
                </td>
                <td style="padding: 16px; text-align: center; font-weight: 700; font-size: 14px; color: var(--color-success);" class="pricing-margin-cell">
                    R$ ${item.margin.toFixed(2)}
                </td>
                <td style="padding: 16px; text-align: center;" class="pricing-margin-pct-cell">
                    <span style="background: rgba(13,148,136,0.12); color: var(--color-success); border: 1px solid rgba(13,148,136,0.25); padding: 4px 10px; border-radius: 20px; font-size: 12px; font-weight: 700;">
                        +${item.margin_pct}%
                    </span>
                </td>
            `;
            tbodyEl.appendChild(tr);

            // Fill summary item
            const sumCard = document.createElement("div");
            sumCard.style.padding = "10px 12px";
            sumCard.style.background = "#fff";
            sumCard.style.borderRadius = "8px";
            sumCard.style.border = "1px solid rgba(13,148,136,0.2)";
            sumCard.id = `summary-card-${item.category}`;
            sumCard.innerHTML = `
                <span style="font-size: 11px; color: var(--text-muted); font-weight: 600; text-transform: uppercase;">${item.label}</span>
                <div style="font-size: 15px; font-weight: 700; color: var(--color-success); margin-top: 2px;">
                    Lucro: R$ ${item.margin.toFixed(2)} <span style="font-size: 11px; font-weight: 600;">(${item.margin_pct}%)</span>
                </div>
            `;
            summaryContentEl.appendChild(sumCard);
        });

        if (loadingEl) loadingEl.style.display = "none";
        if (tableEl) tableEl.style.display = "table";
        if (summaryEl) summaryEl.style.display = "block";
    }

    recalculateRow(inputEl) {
        const tr = inputEl.closest("tr");
        const category = tr.dataset.category;

        const costMetaInput = tr.querySelector(".pricing-cost-meta");
        const priceTenantInput = tr.querySelector(".pricing-price-tenant");
        const marginCell = tr.querySelector(".pricing-margin-cell");
        const marginPctCell = tr.querySelector(".pricing-margin-pct-cell");

        const costMeta = parseFloat(costMetaInput.value) || 0;
        const priceTenant = parseFloat(priceTenantInput.value) || 0;

        const margin = priceTenant - costMeta;
        const marginPct = costMeta > 0 ? ((margin / costMeta) * 100).toFixed(1) : "0.0";

        marginCell.innerText = `R$ ${margin.toFixed(2)}`;
        if (margin < 0) {
            marginCell.style.color = "var(--color-danger)";
            marginPctCell.innerHTML = `
                <span style="background: rgba(225,29,72,0.12); color: var(--color-danger); border: 1px solid rgba(225,29,72,0.25); padding: 4px 10px; border-radius: 20px; font-size: 12px; font-weight: 700;">
                    ${marginPct}%
                </span>
            `;
        } else {
            marginCell.style.color = "var(--color-success)";
            marginPctCell.innerHTML = `
                <span style="background: rgba(13,148,136,0.12); color: var(--color-success); border: 1px solid rgba(13,148,136,0.25); padding: 4px 10px; border-radius: 20px; font-size: 12px; font-weight: 700;">
                    +${marginPct}%
                </span>
            `;
        }

        // Update summary card
        const sumCard = document.getElementById(`summary-card-${category}`);
        if (sumCard) {
            sumCard.querySelector("div").innerHTML = `
                Lucro: R$ ${margin.toFixed(2)} <span style="font-size: 11px; font-weight: 600;">(${marginPct}%)</span>
            `;
        }
    }

    async savePricing() {
        const rows = document.querySelectorAll("#pricing-tbody tr");
        const items = [];

        for (const tr of rows) {
            const category = tr.dataset.category;
            const costMeta = parseFloat(tr.querySelector(".pricing-cost-meta").value);
            const priceTenant = parseFloat(tr.querySelector(".pricing-price-tenant").value);
            const label = tr.querySelector("td div").innerText.trim();

            if (isNaN(costMeta) || isNaN(priceTenant)) {
                this.showToast("Preencha valores numéricos válidos.", "error");
                return;
            }
            if (priceTenant < costMeta) {
                this.showToast(`O preço cobrado (R$ ${priceTenant.toFixed(2)}) não pode ser menor que o custo Meta (R$ ${costMeta.toFixed(2)}) na categoria ${label}.`, "error");
                return;
            }

            items.push({
                category,
                price_tenant: priceTenant,
                cost_meta: costMeta,
                label
            });
        }

        const btnSave = document.getElementById("btn-save-pricing");
        if (btnSave) {
            btnSave.disabled = true;
            btnSave.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> Salvando...`;
        }

        const res = await this.request("/api/superadmin/pricing", "PUT", { items });

        if (btnSave) {
            btnSave.disabled = false;
            btnSave.innerHTML = `<i class="fa-solid fa-floppy-disk"></i> Salvar Preços`;
        }

        if (res) {
            this.showToast("Tabela de precificação salva com sucesso!");
            this.loadPricing();
        }
    }

    // ─── 📄 Gerador de Contrato SaaS Pré-preenchido ─────────────────────────────

    async openContractModal(tenantId) {
        this.activeContractTenantId = tenantId;
        const modal = document.getElementById("tenant-contract-modal");
        if (modal) modal.style.display = "flex";

        const data = await this.request(`/api/superadmin/tenants/${tenantId}/contract-data`);
        if (!data) return;

        const tenantTitle = document.getElementById("contract-modal-tenant-name");
        if (tenantTitle) tenantTitle.innerText = data.tenant_name || "Empresa";

        // Preenche campos do formulário com dados automáticos do banco
        const setVal = (id, val) => {
            const el = document.getElementById(id);
            if (el) el.value = val !== undefined && val !== null ? val : "";
        };

        setVal("contract-client-name", data.tenant_name || "");
        setVal("contract-client-cnpj", data.cnpj || "");
        setVal("contract-client-rep", data.admin_name || "");
        setVal("contract-client-email", data.admin_email || "");
        setVal("contract-client-address", "");
        setVal("contract-plan-name", data.plan_name || "Plano Pro");
        setVal("contract-plan-price", data.price_monthly || 0);
        setVal("contract-max-users", data.max_users || 5);
        setVal("contract-start-date", data.created_at || new Date().toLocaleDateString("pt-BR"));
        setVal("contract-validity", "12 (doze) meses, renovável automaticamente");
        setVal("contract-due-day", "Dia 10 de cada mês");
        setVal("contract-forum", data.default_city || "São Paulo / SP");
        setVal("contract-licensor-name", data.licensor_name || "Q-AURA TECNOLOGIA E SOFTWARES LTDA");
        setVal("contract-licensor-cnpj", data.licensor_cnpj || "55.123.456/0001-89");
        setVal("contract-licensor-address", data.licensor_address || "Av. Paulista, 1000 - Bela Vista, São Paulo - SP, CEP 01310-100");

        this.renderContractPreview();
    }

    renderContractPreview() {
        const getVal = (id, fallback = "") => {
            const el = document.getElementById(id);
            return (el && el.value.trim()) ? el.value.trim() : fallback;
        };

        const clientName = getVal("contract-client-name", "[RAZÃO SOCIAL DO CLIENTE]");
        const clientCnpj = getVal("contract-client-cnpj", "[CNPJ / CPF DO CLIENTE]");
        const clientRep = getVal("contract-client-rep", "[NOME DO REPRESENTANTE LEGAL]");
        const clientEmail = getVal("contract-client-email", "[E-MAIL DO ADMINISTRADOR]");
        const clientAddress = getVal("contract-client-address", "[ENDEREÇO COMPLETO DO CLIENTE]");

        const planName = getVal("contract-plan-name", "Plano Pro");
        const planPriceNum = parseFloat(getVal("contract-plan-price", "0")) || 0;
        const planPrice = planPriceNum.toLocaleString("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
        const maxUsers = getVal("contract-max-users", "5");
        const startDate = getVal("contract-start-date", new Date().toLocaleDateString("pt-BR"));
        const validity = getVal("contract-validity", "12 (doze) meses, renovável automaticamente");
        const dueDay = getVal("contract-due-day", "Dia 10 de cada mês");
        const forum = getVal("contract-forum", "São Paulo / SP");

        const licensorName = getVal("contract-licensor-name", "Q-AURA TECNOLOGIA E SOFTWARES LTDA");
        const licensorCnpj = getVal("contract-licensor-cnpj", "55.123.456/0001-89");
        const licensorAddress = getVal("contract-licensor-address", "Av. Paulista, 1000 - Bela Vista, São Paulo - SP, CEP 01310-100");

        const previewEl = document.getElementById("contract-paper-preview");
        if (!previewEl) return;

        previewEl.innerHTML = `
            <div style="text-align: center; border-bottom: 2px solid #0f172a; padding-bottom: 18px; margin-bottom: 22px;">
                <div style="display: flex; align-items: center; justify-content: center; gap: 10px; margin-bottom: 6px;">
                    <img src="favicon.png" alt="Q-Aura Logo" style="width: 28px; height: 28px; border-radius: 6px; object-fit: cover;">
                    <span style="font-size: 15pt; font-weight: 800; letter-spacing: -0.5px; color: #0f172a;">Q-AURA ATENDIMENTOS OMNICHANNEL</span>
                </div>
                <h1 style="font-size: 13pt; margin: 4px 0; font-weight: 800; text-transform: uppercase; letter-spacing: 0.5px; color: #0f172a; line-height: 1.3;">
                    CONTRATO DE LICENÇA DE USO DE SOFTWARE (SAAS) E PRESTAÇÃO DE SERVIÇOS TÉCNICOS
                </h1>
                <p style="margin: 4px 0 0; font-size: 9.5pt; color: #64748b; font-style: italic;">
                    Instrumento Particular de Prestação de Serviços Digitais e Cessão Temporária de Uso de Plataforma
                </p>
            </div>

            <p style="text-align: justify; margin-bottom: 14px;">
                Pelo presente instrumento particular, de um lado:
            </p>

            <div style="background: #f8fafc; border-left: 3px solid #6366f1; padding: 10px 14px; margin-bottom: 14px; font-size: 11pt; line-height: 1.5;">
                <strong>CONTRATADA (LICENCIANTE):</strong> <strong>${licensorName}</strong>, pessoa jurídica de direito privado, inscrita no CNPJ sob o nº <strong>${licensorCnpj}</strong>, com sede em <strong>${licensorAddress}</strong>, doravante denominada simplesmente <strong>CONTRATADA</strong>; e, de outro lado,
            </div>

            <div style="background: #f8fafc; border-left: 3px solid #10b981; padding: 10px 14px; margin-bottom: 18px; font-size: 11pt; line-height: 1.5;">
                <strong>CONTRATANTE (LICENCIADA):</strong> <strong>${clientName}</strong>, inscrita no CNPJ/CPF sob o nº <strong>${clientCnpj}</strong>, com sede/endereço em <strong>${clientAddress}</strong>, representada neste ato por <strong>${clientRep}</strong> (e-mail cadastrado: <em>${clientEmail}</em>), doravante denominada simplesmente <strong>CONTRATANTE</strong>;
            </div>

            <p style="text-align: justify; margin-bottom: 16px;">
                Têm, entre si, justo e acordado o presente Contrato de Licença de Uso e Prestação de Serviços, que se regerá mediante as seguintes cláusulas e condições:
            </p>

            <h3 style="font-size: 11.5pt; font-weight: 700; color: #0f172a; margin: 18px 0 8px; text-transform: uppercase;">
                CLÁUSULA PRIMEIRA – DO OBJETO
            </h3>
            <p style="text-align: justify; margin-bottom: 10px;">
                <strong>1.1.</strong> O presente contrato tem por objeto a cessão de direito de uso temporário, não exclusivo e intransferível, do software em nuvem (SaaS) denominado <strong>Q-AURA ATENDIMENTOS & CRM OMNICHANNEL</strong>, compreendendo os módulos de painel multi-atendente em tempo real, filas de distribuição por departamentos, robô chatbot automatizado, central de relatórios de métricas e integração oficial com a API do WhatsApp (Meta Cloud API).
            </p>
            <p style="text-align: justify; margin-bottom: 10px;">
                <strong>1.2.</strong> A contratação dá direito estritamente ao acesso e fruição da plataforma hospedada em nuvem, não conferindo à CONTRATANTE qualquer direito sobre o código-fonte, propriedade industrial ou direitos autorais da CONTRATADA.
            </p>

            <h3 style="font-size: 11.5pt; font-weight: 700; color: #0f172a; margin: 18px 0 8px; text-transform: uppercase;">
                CLÁUSULA SEGUNDA – DOS RECURSOS, USUÁRIOS E DISPONIBILIDADE (SLA)
            </h3>
            <p style="text-align: justify; margin-bottom: 10px;">
                <strong>2.1.</strong> A CONTRATANTE terá acesso aos recursos compreendidos no plano <strong>${planName}</strong>, com direito à criação e operação de até <strong>${maxUsers} usuário(s)/operador(es)</strong> no painel de atendimento simultaneamente.
            </p>
            <p style="text-align: justify; margin-bottom: 10px;">
                <strong>2.2.</strong> A CONTRATADA compromete-se a manter uma meta de disponibilidade mensal da plataforma de <strong>99,5% (noventa e nove vírgula cinco por cento)</strong>, excetuando-se indisponibilidades causadas por falhas na infraestrutura global da internet, interrupções ou bloqueios dos serviços da Meta Platforms Inc. (WhatsApp Cloud API) ou manutenções programadas comunicadas previamente.
            </p>

            <h3 style="font-size: 11.5pt; font-weight: 700; color: #0f172a; margin: 18px 0 8px; text-transform: uppercase;">
                CLÁUSULA TERCEIRA – DO PREÇO, FATURAMENTO E CONSUMO META
            </h3>
            <p style="text-align: justify; margin-bottom: 10px;">
                <strong>3.1.</strong> Pelo licenciamento do software e suporte operacional, a CONTRATANTE pagará à CONTRATADA a mensalidade no valor fixo de <strong>R$ ${planPrice}</strong>, com vencimento programado para todo <strong>${dueDay}</strong>.
            </p>
            <p style="text-align: justify; margin-bottom: 10px;">
                <strong>3.2.</strong> Os custos de tarifação oficial por conversas ativas cobradas pela Meta Platforms Inc. (Marketing, Utilidade e Serviço) serão debitados do saldo de créditos pré-pago recarregado pela CONTRATANTE no painel, garantindo total previsibilidade orçamentária.
            </p>
            <p style="text-align: justify; margin-bottom: 10px;">
                <strong>3.3.</strong> O inadimplemento da mensalidade por prazo superior a 10 (dez) dias autoriza a CONTRATADA a suspender preventivamente o envio e recebimento de novas mensagens até a efetiva quitação dos valores em aberto.
            </p>

            <h3 style="font-size: 11.5pt; font-weight: 700; color: #0f172a; margin: 18px 0 8px; text-transform: uppercase;">
                CLÁUSULA QUARTA – DA VIGÊNCIA E RESCISÃO
            </h3>
            <p style="text-align: justify; margin-bottom: 10px;">
                <strong>4.1.</strong> O presente contrato entra em vigor a partir de <strong>${startDate}</strong> e vigerá pelo prazo determinado de <strong>${validity}</strong>.
            </p>
            <p style="text-align: justify; margin-bottom: 10px;">
                <strong>4.2.</strong> Qualquer das partes poderá rescindir a contratação a qualquer tempo mediante aviso prévio por escrito com antecedência mínima de 30 (trinta) dias, não incidindo multas rescisórias abusivas ou cláusulas de fidelidade sobre mensalidades futuras, mantendo-se devidas apenas as obrigações do ciclo corrente.
            </p>

            <h3 style="font-size: 11.5pt; font-weight: 700; color: #0f172a; margin: 18px 0 8px; text-transform: uppercase;">
                CLÁUSULA QUINTA – DA CONFIDENCIALIDADE E PROTEÇÃO DE DADOS (LGPD)
            </h3>
            <p style="text-align: justify; margin-bottom: 10px;">
                <strong>5.1.</strong> As partes obrigam-se a guardar absoluto sigilo sobre quaisquer dados comerciais e estratégicos, bem como atuar em estrita conformidade com a Lei Geral de Proteção de Dados Pessoais (Lei nº 13.709/2018 - LGPD).
            </p>
            <p style="text-align: justify; margin-bottom: 10px;">
                <strong>5.2.</strong> A CONTRATADA atuará na qualidade de operadora, tratando dados pessoais unicamente sob as orientações e finalidades determinadas pela CONTRATANTE (controladora), garantindo isolamento lógico de instâncias e criptografia em trânsito e em repouso.
            </p>

            <h3 style="font-size: 11.5pt; font-weight: 700; color: #0f172a; margin: 18px 0 8px; text-transform: uppercase;">
                CLÁUSULA SEXTA – DO FORO
            </h3>
            <p style="text-align: justify; margin-bottom: 24px;">
                <strong>6.1.</strong> Para dirimir quaisquer litígios oriundos do presente contrato, as partes elegem expressamente o Foro da Comarca de <strong>${forum}</strong>, com renúncia irrevogável a qualquer outro foro, por mais privilegiado que seja.
            </p>

            <div style="margin-top: 24px; text-align: center; margin-bottom: 30px;">
                <p style="margin: 0; font-weight: 600;">E, por estarem assim justas e contratadas, as partes firmam o presente instrumento para que produza todos os efeitos jurídicos e legais.</p>
                <p style="margin-top: 8px; color: #475569;">${forum}, ${startDate}.</p>
            </div>

            <!-- Bloco de Assinaturas -->
            <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 40px; margin-top: 50px; page-break-inside: avoid;">
                <div style="text-align: center;">
                    <div style="border-top: 1px solid #0f172a; padding-top: 8px; font-weight: 700; font-size: 11pt;">${licensorName}</div>
                    <div style="font-size: 9.5pt; color: #475569;">CONTRATADA (Licenciante)</div>
                    <div style="font-size: 9pt; color: #64748b;">CNPJ: ${licensorCnpj}</div>
                </div>
                <div style="text-align: center;">
                    <div style="border-top: 1px solid #0f172a; padding-top: 8px; font-weight: 700; font-size: 11pt;">${clientName}</div>
                    <div style="font-size: 9.5pt; color: #475569;">CONTRATANTE: ${clientRep}</div>
                    <div style="font-size: 9pt; color: #64748b;">CNPJ/CPF: ${clientCnpj}</div>
                </div>
            </div>

            <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 40px; margin-top: 40px; page-break-inside: avoid;">
                <div style="text-align: center;">
                    <div style="border-top: 1px dashed #94a3b8; padding-top: 6px; font-size: 9.5pt; color: #475569;">Testemunha 1 (Nome e CPF)</div>
                </div>
                <div style="text-align: center;">
                    <div style="border-top: 1px dashed #94a3b8; padding-top: 6px; font-size: 9.5pt; color: #475569;">Testemunha 2 (Nome e CPF)</div>
                </div>
            </div>
        `;
    }

    printContract() {
        window.print();
    }

    copyContractText() {
        const preview = document.getElementById("contract-paper-preview");
        if (!preview) return;

        const plainText = preview.innerText || preview.textContent;
        navigator.clipboard.writeText(plainText).then(() => {
            this.showToast("Contrato copiado com sucesso para a área de transferência!");
        }).catch(err => {
            this.showToast("Erro ao copiar contrato: " + err.message, "error");
        });
    }

    closeContractModal() {
        const modal = document.getElementById("tenant-contract-modal");
        if (modal) modal.style.display = "none";
    }
}

// Instantiate and initialize
window.superadminRouter = new SuperadminRouter();
document.addEventListener("DOMContentLoaded", () => {
    window.superadminRouter.init();
});
