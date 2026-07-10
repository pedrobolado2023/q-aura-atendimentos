// Q-aura Front-end App logic (Client router & API client)
const API_URL = window.location.port === "3000" ? "http://localhost:8000" : window.location.origin;

const state = {
    token: localStorage.getItem("qa_token") || null,
    user: JSON.parse(localStorage.getItem("qa_user")) || null,
    tenant_id: localStorage.getItem("qa_tenant_id") || null,
    conversations: [],
    activeConversationId: null,
    ws: null
};

// --- API Client ---
const api = {
    async post(endpoint, data, useAuth = true) {
        const headers = { "Content-Type": "application/json" };
        if (useAuth && state.token) {
            headers["Authorization"] = `Bearer ${state.token}`;
        }
        const response = await fetch(`${API_URL}${endpoint}`, {
            method: "POST",
            headers,
            body: JSON.stringify(data)
        });
        if (!response.ok) {
            const err = await response.json();
            throw new Error(err.detail || "Erro desconhecido");
        }
        return response.json();
    },

    async get(endpoint) {
        const headers = {};
        if (state.token) {
            headers["Authorization"] = `Bearer ${state.token}`;
        }
        const response = await fetch(`${API_URL}${endpoint}`, { headers });
        if (!response.ok) {
            throw new Error("Erro de conexão");
        }
        return response.json();
    }
};

// --- Router ---
const appRouter = {
    navigate(viewName) {
        document.querySelectorAll(".view").forEach(v => v.classList.remove("active"));
        const targetView = document.getElementById(`${viewName}-view`);
        if (targetView) targetView.classList.add("active");
    },

    selectTab(event) {
        event.preventDefault();
        document.querySelectorAll(".menu-item").forEach(i => i.classList.remove("active"));
        event.currentTarget.classList.add("active");
        
        const targetView = event.currentTarget.getAttribute("data-target");
        document.querySelectorAll(".workspace-view").forEach(v => v.classList.remove("active"));
        document.getElementById(targetView).classList.add("active");

        if (targetView === "inbox-view") {
            this.loadConversations();
        } else if (targetView === "admin-view") {
            this.loadAdminTenants();
        }
    },

    init() {
        if (state.token && state.user) {
            this.showMainLayout();
            this.connectWebSocket();
            this.updateProfileUI();
        } else {
            this.navigate("login");
        }
    },

    showMainLayout() {
        document.querySelectorAll(".view").forEach(v => v.classList.remove("active"));
        document.getElementById("main-layout").classList.remove("layout-hidden");
    },

    updateProfileUI() {
        if (state.user) {
            document.getElementById("user-display-name").innerText = state.user.name;
            document.getElementById("user-display-role").innerText = state.user.role.toUpperCase();
            document.getElementById("user-avatar-char").innerText = state.user.name.charAt(0).toUpperCase();

            // Set Meta configurations if they exist
            document.getElementById("webhook-generated-url").innerText = `${API_URL}/api/webhook/${state.tenant_id}`;

            // Show admin only tabs
            if (state.user.role === "administrator") {
                document.querySelectorAll(".master-only").forEach(el => el.style.display = "flex");
            } else {
                document.querySelectorAll(".master-only").forEach(el => el.style.display = "none");
            }
        }
    },

    logout() {
        localStorage.clear();
        state.token = null;
        state.user = null;
        state.tenant_id = null;
        if (state.ws) state.ws.close();
        document.getElementById("main-layout").classList.add("layout-hidden");
        this.navigate("login");
    },

    // --- Data Loaders ---
    async loadConversations() {
        try {
            const listContainer = document.getElementById("convo-list");
            listContainer.innerHTML = "<p class='subtitle' style='padding: 20px;'>Carregando...</p>";
            
            const convos = await api.get("/api/inbox/conversations");
            state.conversations = convos;
            
            listContainer.innerHTML = "";
            if (convos.length === 0) {
                listContainer.innerHTML = "<p class='subtitle' style='padding: 20px;'>Nenhuma conversa.</p>";
                return;
            }

            convos.forEach(c => {
                const item = document.createElement("div");
                item.className = `convo-item ${state.activeConversationId === c.id ? 'active' : ''}`;
                item.onclick = () => this.selectConversation(c.id);
                item.innerHTML = `
                    <div class="avatar">${c.contact_id.substring(0,2).toUpperCase()}</div>
                    <div class="convo-meta">
                        <h4>Hóspede <span class="convo-time">Hoje</span></h4>
                        <p>Status: ${c.status} • Rota: ${c.routing_mode}</p>
                    </div>
                `;
                listContainer.appendChild(item);
            });
        } catch (e) {
            console.error(e);
        }
    },

    async selectConversation(convoId) {
        state.activeConversationId = convoId;
        document.querySelectorAll(".convo-item").forEach(item => item.classList.remove("active"));
        
        // Show conversation pane
        const activeArea = document.getElementById("active-chat-area");
        activeArea.classList.remove("empty");
        activeArea.querySelector(".no-chat-selected").style.display = "none";
        activeArea.querySelector(".chat-wrapper").style.display = "flex";

        // Show guest context panel
        document.getElementById("guest-context").style.display = "block";

        // Load Messages
        try {
            const messages = await api.get(`/api/inbox/conversations/${convoId}/messages`);
            const scroll = document.getElementById("message-scroll");
            scroll.innerHTML = "";
            
            messages.forEach(m => {
                const bubble = document.createElement("div");
                bubble.className = `message-bubble ${m.sender_type === 'contact' ? 'incoming' : 'outgoing'}`;
                bubble.innerText = m.body;
                scroll.appendChild(bubble);
            });
            scroll.scrollTop = scroll.scrollHeight;
        } catch (e) {
            console.error(e);
        }
    },

    async loadAdminTenants() {
        try {
            // Standard simulated data for administrative control
            const tableBody = document.getElementById("tenant-admin-list");
            tableBody.innerHTML = `
                <tr>
                    <td>Resort Costa do Sol</td>
                    <td>costadosol</td>
                    <td><span class="badge">Enterprise</span></td>
                    <td><span class="badge" style="background: rgba(16, 185, 129, 0.1); color: var(--color-success)">Ativo</span></td>
                    <td>12,450 / 50,000</td>
                    <td><button class="btn btn-secondary btn-sm">Suspender</button></td>
                </tr>
                <tr>
                    <td>Hotel Fazenda Colonial</td>
                    <td>fazendacolonial</td>
                    <td><span class="badge">Pro</span></td>
                    <td><span class="badge" style="background: rgba(16, 185, 129, 0.1); color: var(--color-success)">Ativo</span></td>
                    <td>4,120 / 10,000</td>
                    <td><button class="btn btn-secondary btn-sm">Suspender</button></td>
                </tr>
            `;
        } catch (e) {
            console.error(e);
        }
    },

    connectWebSocket() {
        if (state.ws) state.ws.close();
        
        const wsProtocol = window.location.protocol === "https:" ? "wss:" : "ws:";
        const wsHost = window.location.port === "3000" ? "localhost:8000" : window.location.host;
        state.ws = new WebSocket(`${wsProtocol}//${wsHost}/ws/${state.tenant_id}`);
        
        state.ws.onmessage = (event) => {
            const message = JSON.parse(event.data);
            if (message.type === "new_message" && message.conversation_id === state.activeConversationId) {
                // Append message to scroll area
                const scroll = document.getElementById("message-scroll");
                const bubble = document.createElement("div");
                bubble.className = `message-bubble ${message.sender_type === 'contact' ? 'incoming' : 'outgoing'}`;
                bubble.innerText = message.body;
                scroll.appendChild(bubble);
                scroll.scrollTop = scroll.scrollHeight;
            }
            // Reload list to update last message preview
            this.loadConversations();
        };
    }
};

// --- Form Submissions ---

// Login Submit
document.getElementById("login-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const email = document.getElementById("login-email").value;
    const password = document.getElementById("login-password").value;
    
    try {
        const tokenRes = await api.post("/api/auth/login", { email, password }, false);
        state.token = tokenRes.access_token;
        localStorage.setItem("qa_token", state.token);

        // Decode basic claims or mock profiles for simplicity
        // Fetch or simulate user retrieval
        const mockUser = { name: email.split("@")[0], role: "administrator", email };
        state.user = mockUser;
        localStorage.setItem("qa_user", JSON.stringify(mockUser));
        
        // Mock a fixed tenant
        state.tenant_id = "00000000-0000-0000-0000-000000000000";
        localStorage.setItem("qa_tenant_id", state.tenant_id);

        appRouter.showMainLayout();
        appRouter.init();
    } catch (err) {
        alert("Falha no login: " + err.message);
    }
});

// Onboarding Submit
document.getElementById("onboard-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const name = document.getElementById("onboard-name").value;
    const subdomain = document.getElementById("onboard-subdomain").value;
    
    try {
        const tenant = await api.post("/api/auth/onboard", { name, subdomain }, false);
        alert(`Hotel ${tenant.name} criado com sucesso! Faça seu cadastro de usuário agora.`);
        
        // Save tenant reference and prompt signup
        state.tenant_id = tenant.id;
        appRouter.navigate("login");
    } catch (err) {
        alert("Erro no onboarding: " + err.message);
    }
});

// Settings Meta Submit
document.getElementById("settings-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const phone_number_id = document.getElementById("phone-number-id").value;
    const waba_id = document.getElementById("waba-id").value;
    const verify_token = document.getElementById("verify-token").value;
    const permanent_access_token = document.getElementById("permanent-token").value;

    try {
        await api.post("/api/auth/meta-credentials", {
            phone_number_id,
            waba_id,
            verify_token,
            permanent_access_token
        });
        alert("Credenciais salvas com sucesso!");
    } catch (err) {
        alert("Erro ao salvar: " + err.message);
    }
});

// Chat Send Submit
document.getElementById("chat-input-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const input = document.getElementById("chat-message-input");
    const body = input.value.trim();
    if (!body || !state.activeConversationId) return;

    input.value = "";
    try {
        const msg = await api.post(`/api/inbox/send-message?conversation_id=${state.activeConversationId}&body=${encodeURIComponent(body)}`, {});
        
        // Append bubble
        const scroll = document.getElementById("message-scroll");
        const bubble = document.createElement("div");
        bubble.className = "message-bubble outgoing";
        bubble.innerText = msg.body;
        scroll.appendChild(bubble);
        scroll.scrollTop = scroll.scrollHeight;
    } catch (err) {
        alert("Erro ao enviar: " + err.message);
    }
});

// Start router
appRouter.init();
window.appRouter = appRouter;
