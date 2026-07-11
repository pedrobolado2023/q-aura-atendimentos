// Q-aura SaaS - Superadmin Panel Application Logic

class SuperadminRouter {
    constructor() {
        this.token = localStorage.getItem("sa_token") || null;
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
                this.showToast(data.detail || "Não autorizado", "error");
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
        localStorage.removeItem("sa_token");
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
                tableBody.innerHTML = `<tr><td colspan="7" style="padding:32px; text-align:center; color:#64748b;">Nenhuma empresa cadastrada.</td></tr>`;
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

                tr.innerHTML = `
                    <td style="padding: 16px 24px; font-weight: 500; color: #0f172a;">${tenant.name}</td>
                    <td style="padding: 16px 24px; color: #64748b;">${tenant.subdomain}</td>
                    <td style="padding: 16px 24px; color: #64748b;">${tenant.cnpj || "-"}</td>
                    <td style="padding: 16px 24px;"><span class="badge badge-purple">${planName}</span></td>
                    <td style="padding: 16px 24px;">${statusBadge}</td>
                    <td style="padding: 16px 24px; color: #64748b;">${date}</td>
                    <td style="padding: 16px 24px;" class="actions-cell">
                        <button class="btn btn-secondary btn-xs" data-action="edit" data-id="${tenant.id}"><i class="fa-solid fa-pen"></i></button>
                        <button class="btn btn-danger btn-xs" data-action="delete" data-id="${tenant.id}"><i class="fa-solid fa-trash"></i></button>
                    </td>
                `;
                tableBody.appendChild(tr);
            });

            // Attach dynamic listeners for action buttons
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
}

// Instantiate and initialize
window.superadminRouter = new SuperadminRouter();
document.addEventListener("DOMContentLoaded", () => {
    window.superadminRouter.init();
});
