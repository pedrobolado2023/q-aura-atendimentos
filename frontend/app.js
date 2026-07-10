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

// --- Toast Notification Helper ---
function showToast(message, type = "success") {
    const container = document.getElementById("toast-container");
    if (!container) return;
    
    const toast = document.createElement("div");
    toast.className = `toast ${type}`;
    
    const icon = document.createElement("i");
    if (type === "success") {
        icon.className = "fa-solid fa-circle-check";
    } else {
        icon.className = "fa-solid fa-circle-exclamation";
    }
    
    const text = document.createElement("span");
    text.innerText = message;
    
    toast.appendChild(icon);
    toast.appendChild(text);
    container.appendChild(toast);
    
    setTimeout(() => {
        toast.classList.add("fade-out");
        setTimeout(() => {
            toast.remove();
        }, 300);
    }, 4000);
}

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
        } else if (targetView === "settings-view") {
            this.loadMetaSettings();
        }
    },

    async init() {
        if (state.token) {
            try {
                // Busca o perfil real do usuário logado
                const userProfile = await api.get("/api/auth/me");
                state.user = userProfile;
                state.tenant_id = userProfile.tenant_id;
                
                localStorage.setItem("qa_user", JSON.stringify(state.user));
                localStorage.setItem("qa_tenant_id", state.tenant_id);
                
                this.showMainLayout();
                this.connectWebSocket();
                this.updateProfileUI();
                
                // Pré-carrega as configurações da Meta
                this.loadMetaSettings();
            } catch (e) {
                console.error("Erro na autenticação:", e);
                this.logout();
            }
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
                
                const contactName = c.contact ? c.contact.name || c.contact.phone_number : "Hóspede";
                item.innerHTML = `
                    <div class="avatar">${contactName.substring(0,2).toUpperCase()}</div>
                    <div class="convo-meta">
                        <h4>${contactName} <span class="convo-time">Hoje</span></h4>
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
        
        const convo = state.conversations.find(c => c.id === convoId);
        
        // Show conversation pane
        const activeArea = document.getElementById("active-chat-area");
        activeArea.classList.remove("empty");
        activeArea.querySelector(".no-chat-selected").style.display = "none";
        activeArea.querySelector(".chat-wrapper").style.display = "flex";

        // Show guest context panel
        document.getElementById("guest-context").style.display = "block";

        // Update contact details in Right panel
        if (convo && convo.contact) {
            document.getElementById("active-contact-name").innerText = convo.contact.name || "Hóspede";
            document.getElementById("guest-phone").innerText = convo.contact.phone_number;
            document.getElementById("guest-lang").innerText = convo.contact.language === "pt-BR" ? "Português" : convo.contact.language;
            
            const loyalty = convo.contact.loyalty_level || "none";
            document.getElementById("guest-loyalty").innerText = loyalty.charAt(0).toUpperCase() + loyalty.slice(1);
            
            const stageLabels = {
                "lead": "Lead / Novo",
                "qualified": "Qualificado",
                "quotation": "Orçamento Enviado",
                "reservation_pending": "Reserva Pendente",
                "reservation_confirmed": "Reserva Confirmada",
                "lost": "Perdido"
            };
            const stage = convo.contact.sales_funnel_stage;
            document.getElementById("guest-funnel-stage").innerText = stageLabels[stage] || stage.toUpperCase();
        }

        // Load Messages
        try {
            const messages = await api.get(`/api/inbox/conversations/${convoId}/messages`);
            const scroll = document.getElementById("message-scroll");
            scroll.innerHTML = "";
            
            messages.forEach(m => {
                const bubble = document.createElement("div");
                bubble.className = `message-bubble ${m.sender_type === 'contact' ? 'incoming' : 'outgoing'}`;
                
                if (m.message_type === "image" && m.media_url) {
                    const img = document.createElement("img");
                    img.src = m.media_url;
                    img.alt = "Imagem";
                    img.className = "chat-media-image";
                    img.onclick = () => window.open(m.media_url, "_blank");
                    bubble.appendChild(img);
                    
                    if (m.body && m.body !== "[Imagem]") {
                        const caption = document.createElement("div");
                        caption.innerText = m.body;
                        caption.style.marginTop = "8px";
                        bubble.appendChild(caption);
                    }
                } else {
                    bubble.innerText = m.body;
                }
                scroll.appendChild(bubble);
            });
            scroll.scrollTop = scroll.scrollHeight;
        } catch (e) {
            console.error(e);
        }
    },

    async loadAdminTenants() {
        try {
            const tableBody = document.getElementById("tenant-admin-list");
            tableBody.innerHTML = "<tr><td colspan='6' style='padding: 20px; text-align: center;'>Carregando...</td></tr>";
            
            const tenants = await api.get("/api/auth/tenants");
            tableBody.innerHTML = "";
            
            if (tenants.length === 0) {
                tableBody.innerHTML = "<tr><td colspan='6' style='padding: 20px; text-align: center;'>Nenhum hotel cadastrado.</td></tr>";
                return;
            }
            
            tenants.forEach(t => {
                const tr = document.createElement("tr");
                tr.innerHTML = `
                    <td>${t.name}</td>
                    <td>${t.subdomain}</td>
                    <td><span class="badge">${t.plan_type.toUpperCase()}</span></td>
                    <td><span class="badge" style="background: rgba(16, 185, 129, 0.1); color: var(--color-success)">${t.status.toUpperCase()}</span></td>
                    <td>-</td>
                    <td><button class="btn btn-secondary btn-sm" onclick="showToast('Lógica de suspensão de hotel', 'error')">Suspender</button></td>
                `;
                tableBody.appendChild(tr);
            });
        } catch (e) {
            console.error(e);
            document.getElementById("tenant-admin-list").innerHTML = `<tr><td colspan='6' style='padding: 20px; text-align: center; color: var(--color-danger);'>Erro ao carregar hotéis: ${e.message}</td></tr>`;
        }
    },

    async loadMetaSettings() {
        try {
            const creds = await api.get("/api/auth/meta-credentials");
            if (creds) {
                document.getElementById("phone-number-id").value = creds.phone_number_id || "";
                document.getElementById("waba-id").value = creds.waba_id || "";
                document.getElementById("verify-token").value = creds.verify_token || "";
                document.getElementById("permanent-token").value = creds.permanent_access_token || "";
                document.getElementById("webhook-generated-url").innerText = creds.webhook_url;
            }
        } catch (e) {
            document.getElementById("webhook-generated-url").innerText = `${API_URL}/api/webhook/${state.tenant_id}`;
            console.log("Credenciais Meta não encontradas ou não configuradas.");
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
                
                if (message.message_type === "image" && message.media_url) {
                    const img = document.createElement("img");
                    img.src = message.media_url;
                    img.alt = "Imagem";
                    img.className = "chat-media-image";
                    img.onclick = () => window.open(message.media_url, "_blank");
                    bubble.appendChild(img);
                    
                    if (message.body && message.body !== "[Imagem]") {
                        const caption = document.createElement("div");
                        caption.innerText = message.body;
                        caption.style.marginTop = "8px";
                        bubble.appendChild(caption);
                    }
                } else {
                    bubble.innerText = message.body;
                }
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

        // Busca perfil real do usuário
        const userProfile = await api.get("/api/auth/me");
        state.user = userProfile;
        state.tenant_id = userProfile.tenant_id;
        
        localStorage.setItem("qa_user", JSON.stringify(state.user));
        localStorage.setItem("qa_tenant_id", state.tenant_id);

        appRouter.showMainLayout();
        appRouter.init();
    } catch (err) {
        showToast("Falha no login: " + err.message, "error");
    }
});

// Onboarding Submit
document.getElementById("onboard-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const name = document.getElementById("onboard-name").value;
    const subdomain = document.getElementById("onboard-subdomain").value;
    
    try {
        const tenant = await api.post("/api/auth/onboard", { name, subdomain }, false);
        showToast(`Hotel ${tenant.name} criado com sucesso! Faça seu cadastro agora.`, "success");
        
        // Save tenant reference and prompt signup
        state.tenant_id = tenant.id;
        appRouter.navigate("login");
    } catch (err) {
        showToast("Erro no onboarding: " + err.message, "error");
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
        showToast("Credenciais salvas com sucesso!", "success");
    } catch (err) {
        showToast("Erro ao salvar: " + err.message, "error");
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
        showToast("Erro ao enviar: " + err.message, "error");
    }
});

// --- Inbox Tab Filter Click Bindings ---
document.querySelectorAll(".inbox-tabs .tab-btn").forEach(btn => {
    btn.addEventListener("click", async (e) => {
        document.querySelectorAll(".inbox-tabs .tab-btn").forEach(b => b.classList.remove("active"));
        e.currentTarget.classList.add("active");
        const status = e.currentTarget.getAttribute("data-status");
        await appRouter.loadConversations(status);
    });
});

// --- Chat Workspace Actions (Transfer / Resolve) ---
document.getElementById("btn-transfer-chat").addEventListener("click", async () => {
    if (!state.activeConversationId) return;
    try {
        await api.post(`/api/inbox/conversations/${state.activeConversationId}/assign`, {});
        showToast("Conversa assumida com sucesso!", "success");
        
        // Recarrega a fila e limpa a tela de chat ativo
        await appRouter.loadConversations();
        document.getElementById("active-chat-area").classList.add("empty");
        document.getElementById("active-chat-area").querySelector(".no-chat-selected").style.display = "flex";
        document.getElementById("active-chat-area").querySelector(".chat-wrapper").style.display = "none";
        document.getElementById("guest-context").style.display = "none";
    } catch (err) {
        showToast("Erro ao assumir conversa: " + err.message, "error");
    }
});

document.getElementById("btn-resolve-chat").addEventListener("click", async () => {
    if (!state.activeConversationId) return;
    try {
        await api.post(`/api/inbox/conversations/${state.activeConversationId}/resolve`, {});
        showToast("Conversa resolvida!", "success");
        
        // Recarrega a fila e limpa a tela de chat ativo
        await appRouter.loadConversations();
        document.getElementById("active-chat-area").classList.add("empty");
        document.getElementById("active-chat-area").querySelector(".no-chat-selected").style.display = "flex";
        document.getElementById("active-chat-area").querySelector(".chat-wrapper").style.display = "none";
        document.getElementById("guest-context").style.display = "none";
    } catch (err) {
        showToast("Erro ao resolver conversa: " + err.message, "error");
    }
});

// --- PMS Simulator Interactive Actions ---
document.getElementById("btn-pms-quote").addEventListener("click", () => {
    const checkinVal = document.getElementById("pms-checkin").value;
    const checkoutVal = document.getElementById("pms-checkout").value;
    if (!checkinVal || !checkoutVal) {
        showToast("Selecione as datas de Check-in e Check-out.", "error");
        return;
    }
    
    const checkin = new Date(checkinVal);
    const checkout = new Date(checkoutVal);
    
    if (checkout <= checkin) {
        showToast("Check-out deve ser após o Check-in.", "error");
        return;
    }
    
    const diffTime = Math.abs(checkout - checkin);
    const diffDays = Math.ceil(diffTime / (1000 * 60 * 60 * 24));
    const dailyRate = 450;
    const total = dailyRate * diffDays;
    
    document.getElementById("pms-quote-total").innerText = `R$ ${total.toLocaleString("pt-BR")}`;
    document.getElementById("pms-quote-result").querySelector("p:nth-child(3)").innerHTML = `<strong>Total:</strong> R$ ${total.toLocaleString("pt-BR")} (${diffDays} diárias)`;
    document.getElementById("pms-quote-result").style.display = "block";
});

document.getElementById("btn-pms-send").addEventListener("click", () => {
    const checkinVal = document.getElementById("pms-checkin").value;
    const checkoutVal = document.getElementById("pms-checkout").value;
    const checkinFormatted = checkinVal.split("-").reverse().join("/");
    const checkoutFormatted = checkoutVal.split("-").reverse().join("/");
    const totalText = document.getElementById("pms-quote-total").innerText;
    
    const msgInput = document.getElementById("chat-message-input");
    msgInput.value = `Olá! Fiz uma simulação de cotação para o período de ${checkinFormatted} a ${checkoutFormatted}. O valor total fica em ${totalText} para a Suíte Standard. Deseja que eu confirme a sua pré-reserva?`;
    msgInput.focus();
    showToast("Texto de cotação copiado para o chat!", "success");
});

// Start router
appRouter.init();
window.appRouter = appRouter;
