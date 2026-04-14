const { createApp, ref, watch, onMounted, nextTick } = Vue;
const { createRouter, createWebHashHistory } = VueRouter;

const API_URL = '';

const checkAuth = async () => {
  try {
    const res = await axios.get(`${API_URL}/api/me`);
    return res.data;
  } catch (e) { return null; }
};

// ─── Dashboard ────────────────────────────────
const Dashboard = {
  template: `
    <div class="p-8">
      <h1 class="text-3xl font-bold mb-6">학습 대시보드</h1>
      <div class="grid grid-cols-2 md:grid-cols-4 gap-6">
        <div class="bg-white p-6 rounded-lg shadow-md border-t-4 border-blue-500">
          <h2 class="text-sm font-semibold text-gray-500 mb-2">퀴즈 정답률</h2>
          <div class="text-4xl font-bold text-blue-600">{{ stats.correct_rate }}%</div>
        </div>
        <div class="bg-white p-6 rounded-lg shadow-md border-t-4 border-green-500">
          <h2 class="text-sm font-semibold text-gray-500 mb-2">학습 진도</h2>
          <div class="text-4xl font-bold text-green-600">{{ stats.progress }}%</div>
        </div>
        <div class="bg-white p-6 rounded-lg shadow-md border-t-4 border-purple-500">
          <h2 class="text-sm font-semibold text-gray-500 mb-2">업로드 문서</h2>
          <div class="text-4xl font-bold text-purple-600">{{ stats.total_docs }}</div>
        </div>
        <div class="bg-white p-6 rounded-lg shadow-md border-t-4 border-yellow-500">
          <h2 class="text-sm font-semibold text-gray-500 mb-2">총 질문 수</h2>
          <div class="text-4xl font-bold text-yellow-600">{{ stats.total_queries }}</div>
        </div>
      </div>
      <div v-if="stats.total_docs === 0" class="mt-10 bg-blue-50 border border-blue-200 rounded-xl p-6 text-center">
        <p class="text-blue-700 font-semibold mb-3">아직 업로드된 문서가 없습니다.</p>
        <router-link to="/upload" class="inline-block bg-blue-600 text-white px-5 py-2 rounded-lg hover:bg-blue-700 transition-colors">자료 업로드하기</router-link>
      </div>
    </div>
  `,
  setup() {
    const stats = ref({ correct_rate: 0, progress: 0, total_docs: 0, total_queries: 0 });
    onMounted(async () => {
      try {
        const res = await axios.get(`${API_URL}/api/stats`);
        stats.value = res.data;
      } catch (e) { console.error('stats 로드 실패', e); }
    });
    return { stats };
  }
};

// ─── Login ───────────────────────────────────
const Login = {
  template: `
    <div class="min-h-screen flex items-center justify-center bg-gray-100 px-4">
      <div class="bg-white p-10 rounded-2xl shadow-xl text-center max-w-md w-full border border-gray-100">
        <div class="w-20 h-20 bg-blue-600 rounded-2xl mx-auto mb-6 flex items-center justify-center shadow-lg shadow-blue-200">
          <svg class="w-12 h-12 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5c1.747 0 3.332.477 4.5 1.253v13C19.832 18.477 18.247 18 16.5 18c-1.746 0-3.332.477-4.5 1.253"></path></svg>
        </div>
        <h2 class="text-3xl font-extrabold text-gray-900 mb-2">RAG Study</h2>
        <p class="text-gray-500 mb-8">AI와 함께하는 스마트한 학습 플랫폼</p>
        <a href="/login" class="flex items-center justify-center gap-3 bg-white border border-gray-300 px-6 py-4 rounded-xl hover:bg-gray-50 transition-all active:scale-95 shadow-sm">
          <img src="https://www.google.com/favicon.ico" class="w-5 h-5">
          <span class="font-bold text-gray-700">Google 계정으로 시작하기</span>
        </a>
      </div>
    </div>
  `
};

// ─── Upload ──────────────────────────────────
const Upload = {
  template: `
    <div class="p-8">
      <h1 class="text-3xl font-bold mb-6">자료 업로드</h1>

      <!-- 업로드 영역 -->
      <div class="bg-white rounded-xl shadow p-6 mb-8">
        <div
          class="border-2 border-dashed rounded-xl p-10 text-center transition-colors cursor-pointer"
          :class="dragOver ? 'border-blue-500 bg-blue-50' : 'border-gray-300 hover:border-blue-400'"
          @dragover.prevent="dragOver = true"
          @dragleave="dragOver = false"
          @drop.prevent="onDrop"
          @click="$refs.fileInput.click()"
        >
          <svg class="w-12 h-12 mx-auto mb-3 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12"/>
          </svg>
          <p class="text-gray-600 font-medium">
            {{ selectedFile ? selectedFile.name : 'PDF 또는 텍스트 파일을 드래그하거나 클릭하세요' }}
          </p>
          <p class="text-gray-400 text-sm mt-1">지원 형식: .pdf, .txt</p>
          <input ref="fileInput" type="file" accept=".pdf,.txt" class="hidden" @change="onFileChange">
        </div>

        <div class="mt-4 flex items-center gap-4">
          <button
            @click="uploadFile"
            :disabled="!selectedFile || uploading"
            class="bg-blue-600 text-white px-6 py-2.5 rounded-lg font-semibold hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors flex items-center gap-2"
          >
            <span v-if="uploading" class="animate-spin inline-block w-4 h-4 border-2 border-white border-t-transparent rounded-full"></span>
            {{ uploading ? '업로드 중...' : '업로드' }}
          </button>
          <p v-if="message" :class="messageOk ? 'text-green-600' : 'text-red-500'" class="font-medium">{{ message }}</p>
        </div>
      </div>

      <!-- 문서 목록 -->
      <div v-if="documents.length > 0" class="bg-white rounded-xl shadow p-6">
        <div class="flex items-center justify-between mb-4">
          <h2 class="text-lg font-bold">업로드된 문서</h2>
          <span v-if="totalPages > 1" class="text-sm text-gray-400">
            전체 {{ documents.length }}개 &nbsp;·&nbsp; {{ currentPage }} / {{ totalPages }} 페이지
          </span>
        </div>

        <table class="w-full text-sm">
          <thead>
            <tr class="text-left text-gray-400 border-b">
              <th class="pb-2 font-medium w-8">#</th>
              <th class="pb-2 font-medium">파일명</th>
              <th class="pb-2 font-medium">형식</th>
              <th class="pb-2 font-medium">업로드 일시</th>
              <th class="pb-2 font-medium">바로가기</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(doc, i) in pagedDocuments" :key="doc.id" class="border-b last:border-0 hover:bg-gray-50">
              <td class="py-3 text-gray-400 text-xs">{{ (currentPage - 1) * pageSize + i + 1 }}</td>
              <td class="py-3 text-gray-800 font-medium max-w-xs truncate">{{ doc.filename }}</td>
              <td class="py-3">
                <span class="bg-blue-100 text-blue-700 text-xs font-semibold px-2 py-0.5 rounded uppercase">{{ doc.file_type }}</span>
              </td>
              <td class="py-3 text-gray-500">{{ formatDate(doc.uploaded_at) }}</td>
              <td class="py-3 flex gap-2">
                <button @click="goToChat(doc.id)" class="text-xs bg-green-100 text-green-700 px-3 py-1 rounded-lg hover:bg-green-200 transition-colors font-medium">채팅하기</button>
                <button @click="goToQuestions(doc.id)" class="text-xs bg-purple-100 text-purple-700 px-3 py-1 rounded-lg hover:bg-purple-200 transition-colors font-medium">문제 생성</button>
                <button @click="deleteDocument(doc.id, doc.filename)" class="text-xs bg-red-100 text-red-600 px-3 py-1 rounded-lg hover:bg-red-200 transition-colors font-medium">삭제</button>
              </td>
            </tr>
          </tbody>
        </table>

        <!-- 페이지네이션 -->
        <div v-if="totalPages > 1" class="flex items-center justify-center gap-1 mt-6">
          <button
            @click="currentPage = 1"
            :disabled="currentPage === 1"
            class="px-2 py-1 rounded text-sm text-gray-500 hover:bg-gray-100 disabled:opacity-30 disabled:cursor-not-allowed"
            title="처음"
          >«</button>
          <button
            @click="currentPage--"
            :disabled="currentPage === 1"
            class="px-3 py-1 rounded text-sm text-gray-500 hover:bg-gray-100 disabled:opacity-30 disabled:cursor-not-allowed"
          >‹</button>

          <button
            v-for="p in pageNumbers"
            :key="p"
            @click="typeof p === 'number' && (currentPage = p)"
            :class="p === currentPage
              ? 'bg-blue-600 text-white font-semibold'
              : p === '...' ? 'cursor-default text-gray-400' : 'text-gray-600 hover:bg-gray-100'"
            class="w-9 h-9 rounded-lg text-sm transition-colors"
          >{{ p }}</button>

          <button
            @click="currentPage++"
            :disabled="currentPage === totalPages"
            class="px-3 py-1 rounded text-sm text-gray-500 hover:bg-gray-100 disabled:opacity-30 disabled:cursor-not-allowed"
          >›</button>
          <button
            @click="currentPage = totalPages"
            :disabled="currentPage === totalPages"
            class="px-2 py-1 rounded text-sm text-gray-500 hover:bg-gray-100 disabled:opacity-30 disabled:cursor-not-allowed"
            title="마지막"
          >»</button>
        </div>
      </div>
    </div>
  `,
  setup() {
    const router    = VueRouter.useRouter();
    const documents = ref([]);
    const selectedFile = ref(null);
    const uploading = ref(false);
    const dragOver  = ref(false);
    const message   = ref('');
    const messageOk = ref(true);

    const pageSize    = 20;
    const currentPage = ref(1);

    const totalPages = Vue.computed(() => Math.ceil(documents.value.length / pageSize));

    const pagedDocuments = Vue.computed(() => {
      const start = (currentPage.value - 1) * pageSize;
      return documents.value.slice(start, start + pageSize);
    });

    // 페이지 번호 배열 생성 (최대 7개 표시, 초과 시 ... 삽입)
    const pageNumbers = Vue.computed(() => {
      const total = totalPages.value;
      const cur   = currentPage.value;
      if (total <= 7) return Array.from({ length: total }, (_, i) => i + 1);
      const pages = [];
      if (cur <= 4) {
        pages.push(1, 2, 3, 4, 5, '...', total);
      } else if (cur >= total - 3) {
        pages.push(1, '...', total - 4, total - 3, total - 2, total - 1, total);
      } else {
        pages.push(1, '...', cur - 1, cur, cur + 1, '...', total);
      }
      return pages;
    });

    const fetchDocuments = async () => {
      try {
        const docRes = await axios.get(`${API_URL}/api/documents`);
        documents.value = docRes.data;
        currentPage.value = 1;
      } catch (e) { console.error(e); }
    };

    const onFileChange = (e) => { selectedFile.value = e.target.files[0] || null; };
    const onDrop = (e) => {
      dragOver.value = false;
      selectedFile.value = e.dataTransfer.files[0] || null;
    };

    const uploadFile = async () => {
      if (!selectedFile.value || uploading.value) return;
      uploading.value = true;
      message.value   = '';
      const formData  = new FormData();
      formData.append('file', selectedFile.value);
      try {
        await axios.post(`${API_URL}/api/upload`, formData);
        messageOk.value    = true;
        message.value      = `"${selectedFile.value.name}" 업로드 성공!`;
        selectedFile.value = null;
        await fetchDocuments();
      } catch (e) {
        messageOk.value = false;
        message.value   = '업로드 실패: ' + (e.response?.data?.detail || e.message);
      } finally {
        uploading.value = false;
      }
    };

    const deleteDocument = async (id, filename) => {
      if (!confirm(`"${filename}" 문서를 정말 삭제하시겠습니까? 관련 대화 및 문제 데이터가 모두 삭제됩니다.`)) return;
      try {
        await axios.delete(`${API_URL}/api/documents/${id}`);
        await fetchDocuments();
        alert('삭제되었습니다.');
      } catch (e) {
        alert('삭제 실패: ' + (e.response?.data?.detail || e.message));
      }
    };

    const formatDate = (iso) => {
      if (!iso) return '-';
      const date = new Date(iso);
      return date.toLocaleString('ko-KR', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
    };

    const goToChat      = (id) => router.push({ path: '/chat',      query: { document_id: id } });
    const goToQuestions = (id) => router.push({ path: '/questions', query: { document_id: id } });

    onMounted(fetchDocuments);
    return {
      documents, selectedFile, uploading, dragOver, message, messageOk,
      currentPage, pageSize, totalPages, pagedDocuments, pageNumbers,
      onFileChange, onDrop, uploadFile, deleteDocument, formatDate, goToChat, goToQuestions,
    };
  }
};

// ─── Chat ────────────────────────────────────
const Chat = {
  template: `
    <div class="flex flex-col h-screen">
      <!-- 헤더 -->
      <div class="bg-white border-b px-6 py-4 flex flex-wrap items-center gap-4 shadow-sm">
        <h1 class="text-xl font-bold">챗봇 학습</h1>
        <div class="flex items-center gap-2">
          <span class="text-sm font-medium text-gray-500">문서:</span>
          <select
            v-model="selectedDocId"
            class="border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
          >
            <option :value="null" disabled>문서를 선택하세요</option>
            <option v-for="doc in documents" :key="doc.id" :value="doc.id">{{ doc.filename }}</option>
          </select>
        </div>
        <div class="flex items-center gap-2">
          <span class="text-sm font-medium text-gray-500">모델:</span>
          <select
            v-model="selectedProvider"
            class="border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
          >
            <option v-for="m in availableModels" :key="m.id" :value="m.id">{{ m.name }}</option>
          </select>
        </div>
        <router-link v-if="documents.length === 0" to="/upload" class="text-blue-600 text-sm hover:underline">파일 업로드하기 →</router-link>
      </div>

      <!-- 메시지 영역 -->
      <div ref="msgBox" class="flex-1 overflow-y-auto p-6 space-y-4 bg-gray-50">
        <div v-if="!selectedDocId && documents.length === 0" class="flex flex-col items-center justify-center h-full text-center">
          <svg class="w-16 h-16 text-gray-300 mb-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"/></svg>
          <p class="text-gray-500 font-medium">파일을 먼저 업로드해주세요.</p>
          <router-link to="/upload" class="mt-3 text-blue-600 hover:underline text-sm">업로드 페이지 이동 →</router-link>
        </div>
        <div v-else-if="!selectedDocId" class="flex items-center justify-center h-full">
          <p class="text-gray-400">위에서 문서를 선택하면 채팅을 시작할 수 있습니다.</p>
        </div>

        <template v-else>
          <div v-if="messages.length === 0" class="flex items-center justify-center h-32">
            <p class="text-gray-400 text-sm">질문을 입력해 문서 내용을 학습해보세요!</p>
          </div>
          <div v-for="(msg, i) in messages" :key="i" :class="msg.role === 'user' ? 'flex justify-end' : 'flex justify-start'">
            <div
              v-if="msg.content && msg.content.trim()"
              :class="msg.role === 'user'
                ? 'bg-blue-600 text-white rounded-2xl rounded-tr-sm px-4 py-3 max-w-lg shadow'
                : 'bg-white text-gray-800 rounded-2xl rounded-tl-sm px-4 py-3 max-w-lg shadow border border-gray-100'"
              class="text-sm leading-relaxed whitespace-pre-wrap"
            >
              {{ msg.content }}
              <!-- AI 답변일 경우 모델 및 출처 표시 -->
              <div v-if="msg.role === 'assistant'" class="mt-2 pt-2 border-t border-gray-100 flex flex-col gap-1 text-[10px] text-gray-400">
                <div v-if="msg.model" class="flex items-center gap-1">
                  <span class="font-bold text-blue-400 uppercase">● {{ msg.model }}</span>
                </div>
                <div v-if="msg.sources && msg.sources.length > 0" class="flex items-center gap-1">
                  <span class="font-medium italic">출처: {{ msg.sources.join(', ') }}</span>
                </div>
              </div>
            </div>
          </div>
          <!-- 타이핑 인디케이터 -->
          <div v-if="loading" class="flex justify-start">
            <div class="bg-white border border-gray-100 rounded-2xl rounded-tl-sm px-4 py-3 shadow">
              <div class="flex gap-1">
                <span class="w-2 h-2 bg-gray-400 rounded-full animate-bounce" style="animation-delay:0s"></span>
                <span class="w-2 h-2 bg-gray-400 rounded-full animate-bounce" style="animation-delay:.15s"></span>
                <span class="w-2 h-2 bg-gray-400 rounded-full animate-bounce" style="animation-delay:.3s"></span>
              </div>
            </div>
          </div>
        </template>
      </div>

      <!-- 입력 영역 -->
      <div class="bg-white border-t px-6 py-4">
        <div class="flex gap-3">
          <input
            v-model="inputText"
            @keydown.enter.prevent="sendMessage"
            :disabled="!selectedDocId || loading"
            type="text"
            placeholder="질문을 입력하세요... (Enter로 전송)"
            class="flex-1 border border-gray-300 rounded-xl px-4 py-3 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:bg-gray-100"
          >
          <button
            @click="sendMessage"
            :disabled="!selectedDocId || !inputText.trim() || loading"
            class="bg-blue-600 text-white px-6 py-3 rounded-xl font-semibold hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >전송</button>
        </div>
      </div>
    </div>
  `,
  setup() {
    const route        = VueRouter.useRoute();
    const documents    = ref([]);
    const selectedDocId = ref(null);
    const selectedProvider = ref('openai');
    const availableModels  = ref([]);
    const messages     = ref([]);
    const inputText    = ref('');
    const loading      = ref(false);
    const msgBox       = ref(null);

    const scrollBottom = () => nextTick(() => {
      if (msgBox.value) msgBox.value.scrollTop = msgBox.value.scrollHeight;
    });

    const fetchHistory = async (docId) => {
      if (!docId) {
        messages.value = [];
        return;
      }
      try {
        const res = await axios.get(`${API_URL}/api/chat/history`, {
          params: { document_id: docId }
        });
        messages.value = res.data;
        scrollBottom();
      } catch (e) {
        console.error('대화 내역 로드 실패', e);
      }
    };

    watch(selectedDocId, (newId) => {
      fetchHistory(newId);
    });

    const sendMessage = async () => {
      if (!inputText.value.trim() || !selectedDocId.value || loading.value) return;
      const query = inputText.value.trim();
      messages.value.push({ role: 'user', content: query });
      inputText.value = '';
      loading.value   = true;
      scrollBottom();
      try {
        const res = await axios.get(`${API_URL}/api/chat`, {
          params: { query, document_id: selectedDocId.value, provider: selectedProvider.value },
        });
        messages.value.push({ 
          role: 'assistant', 
          content: res.data.response,
          model: res.data.model,
          sources: res.data.sources
        });
      } catch (e) {
        messages.value.push({ role: 'assistant', content: '오류가 발생했습니다: ' + (e.response?.data?.detail || e.message) });
      } finally {
        loading.value = false;
        scrollBottom();
      }
    };

    onMounted(async () => {
      try {
        const [docRes, modelRes] = await Promise.all([
          axios.get(`${API_URL}/api/documents`),
          axios.get(`${API_URL}/api/available-models`)
        ]);
        documents.value = docRes.data;
        availableModels.value = modelRes.data;
        
        if (route.query.provider) {
          selectedProvider.value = route.query.provider;
        } else if (availableModels.value.length > 0) {
          selectedProvider.value = availableModels.value[0].id;
        }
      } catch (e) { console.error(e); }

      if (route.query.document_id) {
        selectedDocId.value = parseInt(route.query.document_id);
        fetchHistory(selectedDocId.value);
      }
    });

    return { documents, selectedDocId, selectedProvider, availableModels, messages, inputText, loading, msgBox, sendMessage };
  }
};

// ─── Questions ───────────────────────────────
const Questions = {
  template: `
    <div class="p-8">
      <h1 class="text-3xl font-bold mb-6">문제은행</h1>

      <div class="bg-white rounded-xl shadow p-6 mb-6 flex flex-wrap items-center gap-4">
        <div class="flex items-center gap-2">
          <span class="text-sm font-medium text-gray-500">문서:</span>
          <select
            v-model="selectedDocId"
            class="border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-purple-500"
          >
            <option :value="null" disabled>문서를 선택하세요</option>
            <option v-for="doc in documents" :key="doc.id" :value="doc.id">{{ doc.filename }}</option>
          </select>
        </div>
        <div class="flex items-center gap-2">
          <span class="text-sm font-medium text-gray-500">모델:</span>
          <select
            v-model="selectedProvider"
            class="border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-purple-500"
          >
            <option v-for="m in availableModels" :key="m.id" :value="m.id">{{ m.name }}</option>
          </select>
        </div>
        <button
          @click="generateQuestions"
          :disabled="!selectedDocId || generating"
          class="bg-purple-600 text-white px-5 py-2 rounded-lg font-semibold hover:bg-purple-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors flex items-center gap-2"
        >
          <span v-if="generating" class="animate-spin inline-block w-4 h-4 border-2 border-white border-t-transparent rounded-full"></span>
          {{ generating ? 'AI가 문제를 생성 중...' : '문제 생성하기' }}
        </button>
        <button
          v-if="questions.length > 0"
          @click="clearQuestions"
          class="bg-gray-200 text-gray-700 px-5 py-2 rounded-lg font-semibold hover:bg-gray-300 transition-colors"
        >
          문제 비우기
        </button>
        <button
          v-if="hasWrongQuestions"
          @click="reviewMode = !reviewMode"
          :class="reviewMode ? 'bg-red-600 text-white hover:bg-red-700' : 'bg-red-100 text-red-700 hover:bg-red-200'"
          class="px-5 py-2 rounded-lg font-semibold transition-colors flex items-center gap-2"
        >
          {{ reviewMode ? '전체 보기' : '틀린 문제 복습하기' }}
        </button>
        <router-link v-if="documents.length === 0" to="/upload" class="text-purple-600 text-sm hover:underline">파일 업로드하기 →</router-link>
      </div>

      <!-- 점수 배너 -->
      <div v-if="submitted" class="mb-6 p-5 rounded-xl text-center font-bold text-lg"
           :class="score.correct / score.total >= 0.7 ? 'bg-green-100 text-green-700' : 'bg-orange-100 text-orange-700'">
        {{ score.total }}문제 중 {{ score.correct }}문제 정답 ({{ Math.round(score.correct / score.total * 100) }}%)
        <button @click="resetQuiz" class="ml-4 text-sm font-normal underline">다시 풀기</button>
      </div>

      <!-- 문제 없음 안내 -->
      <div v-if="!generating && questions.length === 0 && selectedDocId" class="bg-gray-50 rounded-xl p-8 text-center text-gray-400">
        이 문서의 문제가 없습니다. "문제 생성하기"를 클릭하세요.
      </div>
      <div v-if="!selectedDocId" class="bg-gray-50 rounded-xl p-8 text-center text-gray-400">
        문서를 선택하면 문제를 불러오거나 생성할 수 있습니다.
      </div>

      <!-- 문제 카드 목록 -->
      <div v-if="displayedQuestions.length > 0" class="space-y-6">
        <div v-for="(q, qi) in displayedQuestions" :key="q.id" class="bg-white rounded-xl shadow p-6 relative">
          <!-- 상태 배지 -->
          <div class="absolute top-4 right-6 flex gap-2">
            <div v-if="q.status === 'solved'" class="bg-green-100 text-green-700 text-[10px] font-bold px-2 py-0.5 rounded-full border border-green-200">
              ✓ 학습 완료
            </div>
            <div v-if="q.status === 'wrong'" class="bg-red-100 text-red-700 text-[10px] font-bold px-2 py-0.5 rounded-full border border-red-200">
              ✗ 다시 풀기
            </div>
          </div>
          <p class="font-semibold text-gray-800 mb-4 pr-24">
            <span class="text-purple-600 mr-2">Q{{ reviewMode ? qi + 1 : questions.indexOf(q) + 1 }}.</span>{{ q.question }}
          </p>
          <div class="space-y-2">
            <label
              v-for="choice in q.choices"
              :key="choice"
              class="flex items-center gap-3 p-3 rounded-lg border cursor-pointer transition-colors"
              :class="choiceClass(q.id, choice)"
            >
              <input
                type="radio"
                :name="'q' + q.id"
                :value="choice"
                v-model="userAnswers[q.id]"
                :disabled="submitted"
                class="accent-purple-600"
              >
              <span class="text-sm">{{ choice }}</span>
              <span v-if="submitted && choice === results[q.id]?.correct_answer" class="ml-auto text-green-600 text-xs font-bold">정답</span>
            </label>
          </div>
        </div>

        <!-- 채점 버튼 -->
        <div v-if="!submitted" class="flex justify-center pt-2">
          <button
            @click="submitAnswers"
            :disabled="submitting || Object.keys(userAnswers).length < questions.length"
            class="bg-blue-600 text-white px-10 py-3 rounded-xl font-bold text-lg hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors flex items-center gap-2"
          >
            <span v-if="submitting" class="animate-spin inline-block w-5 h-5 border-2 border-white border-t-transparent rounded-full"></span>
            {{ submitting ? '채점 중...' : '채점하기' }}
          </button>
        </div>
      </div>
    </div>
  `,
  setup() {
    const route        = VueRouter.useRoute();
    const documents    = ref([]);
    const selectedDocId = ref(null);
    const selectedProvider = ref('openai');
    const availableModels  = ref([]);
    const questions    = ref([]);
    const userAnswers  = ref({});
    const results      = ref({});
    const generating   = ref(false);
    const submitting   = ref(false);
    const submitted    = ref(false);
    const score        = ref({ correct: 0, total: 0 });
    const reviewMode   = ref(false);

    const hasWrongQuestions = Vue.computed(() => questions.value.some(q => q.status === 'wrong'));
    const displayedQuestions = Vue.computed(() => {
      if (reviewMode.value) {
        return questions.value.filter(q => q.status === 'wrong');
      }
      return questions.value;
    });

    const fetchQuestions = async (docId) => {
      try {
        const res = await axios.get(`${API_URL}/api/questions`, { params: { document_id: docId } });
        questions.value = res.data;
      } catch (e) { console.error(e); }
    };

    watch(selectedDocId, async (newId) => {
      if (!newId) return;
      resetQuiz();
      await fetchQuestions(newId);
    });

    const generateQuestions = async () => {
      if (!selectedDocId.value || generating.value) return;
      generating.value = true;
      resetQuiz();
      try {
        const res = await axios.post(`${API_URL}/api/questions/generate`, { document_id: selectedDocId.value }, {
            params: { provider: selectedProvider.value }
        });
        // 생성 후 목록 다시 불러오기 (is_solved 정보 포함을 위해)
        await fetchQuestions(selectedDocId.value);
      } catch (e) {
        alert('문제 생성 실패: ' + (e.response?.data?.detail || e.message));
      } finally {
        generating.value = false;
      }
    };

    const clearQuestions = async () => {
      if (!selectedDocId.value) return;
      if (!confirm('이 문서와 관련된 모든 문제를 삭제하시겠습니까? (풀이 기록도 함께 삭제됩니다.)')) return;
      
      try {
        await axios.delete(`${API_URL}/api/questions`, {
          params: { document_id: selectedDocId.value }
        });
        questions.value = [];
        resetQuiz();
        alert('문제함이 비워졌습니다.');
      } catch (e) {
        alert('삭제 실패: ' + (e.response?.data?.detail || e.message));
      }
    };

    const submitAnswers = async () => {
      if (submitting.value) return;
      submitting.value = true;
      const resultMap = {};
      let correct = 0;
      for (const q of questions.value) {
        const answer = userAnswers.value[q.id] ?? '';
        try {
          const res = await axios.post(`${API_URL}/api/questions/attempt`, {
            question_id: q.id,
            user_answer: answer,
          });
          resultMap[q.id] = res.data;
          if (res.data.is_correct) correct++;
        } catch (e) {
          resultMap[q.id] = { is_correct: false, correct_answer: '' };
        }
      }
      results.value   = resultMap;
      score.value     = { correct, total: questions.value.length };
      submitted.value = true;
      submitting.value = false;
      // 맞힌 문제 배지 업데이트를 위해 목록 새로고침
      await fetchQuestions(selectedDocId.value);
    };

    const resetQuiz = () => {
      userAnswers.value = {};
      results.value     = {};
      submitted.value   = false;
      score.value       = { correct: 0, total: 0 };
    };

    // 선택지 색상 클래스
    const choiceClass = (qId, choice) => {
      if (!submitted.value) {
        return userAnswers.value[qId] === choice
          ? 'border-purple-400 bg-purple-50'
          : 'border-gray-200 hover:border-purple-300 hover:bg-purple-50';
      }
      const r = results.value[qId];
      if (!r) return 'border-gray-200';
      if (choice === r.correct_answer) return 'border-green-400 bg-green-50';
      if (choice === userAnswers.value[qId] && !r.is_correct) return 'border-red-400 bg-red-50';
      return 'border-gray-200';
    };

    onMounted(async () => {
      try {
        const [docRes, modelRes] = await Promise.all([
          axios.get(`${API_URL}/api/documents`),
          axios.get(`${API_URL}/api/available-models`)
        ]);
        documents.value = docRes.data;
        availableModels.value = modelRes.data;
        
        if (route.query.provider) {
          selectedProvider.value = route.query.provider;
        } else if (availableModels.value.length > 0) {
          selectedProvider.value = availableModels.value[0].id;
        }
      } catch (e) { console.error(e); }

      if (route.query.document_id) {
        selectedDocId.value = parseInt(route.query.document_id);
      }
    });

    return {
      documents, selectedDocId, selectedProvider, availableModels, questions, userAnswers, results,
      generating, submitting, submitted, score,
      reviewMode, hasWrongQuestions, displayedQuestions,
      generateQuestions, clearQuestions, submitAnswers, resetQuiz, choiceClass,
    };
  }
};

// ─── AdminDashboard ───────────────────────────
const AdminDashboard = {
  template: `
    <div class="p-8">
      <h1 class="text-3xl font-bold mb-8">관리자 대시보드</h1>

      <!-- 요약 카드 -->
      <div class="grid grid-cols-2 md:grid-cols-5 gap-4 mb-8">
        <div class="bg-white rounded-xl shadow p-5 border-t-4 border-blue-500 text-center">
          <p class="text-xs text-gray-400 mb-1">전체 사용자</p>
          <p class="text-3xl font-black text-blue-600">{{ ov.summary.total_users }}</p>
        </div>
        <div class="bg-white rounded-xl shadow p-5 border-t-4 border-green-500 text-center">
          <p class="text-xs text-gray-400 mb-1">업로드 문서</p>
          <p class="text-3xl font-black text-green-600">{{ ov.summary.total_docs }}</p>
        </div>
        <div class="bg-white rounded-xl shadow p-5 border-t-4 border-purple-500 text-center">
          <p class="text-xs text-gray-400 mb-1">총 RAG 쿼리</p>
          <p class="text-3xl font-black text-purple-600">{{ ov.summary.total_queries }}</p>
        </div>
        <div class="bg-white rounded-xl shadow p-5 border-t-4 border-yellow-500 text-center">
          <p class="text-xs text-gray-400 mb-1">학습 청크 수</p>
          <p class="text-3xl font-black text-yellow-600">{{ ov.summary.total_chunks }}</p>
        </div>
        <div class="bg-white rounded-xl shadow p-5 border-t-4 border-red-400 text-center">
          <p class="text-xs text-gray-400 mb-1">퀴즈 정답률</p>
          <p class="text-3xl font-black text-red-500">{{ ov.summary.quiz_accuracy }}%</p>
        </div>
      </div>

      <!-- 모델별 성능 통계 -->
      <div class="bg-white rounded-xl shadow p-6 mb-8">
        <h2 class="text-lg font-bold mb-4">모델별 성능 분석</h2>
        <div class="overflow-x-auto">
          <table class="w-full text-sm">
            <thead>
              <tr class="text-left text-gray-400 border-b">
                <th class="pb-2 font-medium">모델명</th>
                <th class="pb-2 font-medium text-center">호출 횟수</th>
                <th class="pb-2 font-medium text-center">평균 지연시간(ms)</th>
                <th class="pb-2 font-medium">성능 상태</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="ms in ov.model_stats" :key="ms.model" class="border-b last:border-0">
                <td class="py-3 font-bold text-gray-700 uppercase">{{ ms.model }}</td>
                <td class="py-3 text-center text-blue-600 font-semibold">{{ ms.count }}회</td>
                <td class="py-3 text-center text-gray-600">{{ ms.avg_latency }}ms</td>
                <td class="py-3">
                  <span :class="ms.avg_latency < 2000 ? 'bg-green-100 text-green-700' : 'bg-orange-100 text-orange-700'"
                        class="text-[10px] px-2 py-0.5 rounded-full font-bold">
                    {{ ms.avg_latency < 2000 ? '쾌적' : '지연' }}
                  </span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <!-- RAG 평가 지표 -->
      <div class="bg-white rounded-xl shadow p-6 mb-8">
        <h2 class="text-lg font-bold mb-4">RAG 모델 성능 평가</h2>
        <div v-if="noEval" class="text-gray-400 text-sm">아직 평가 데이터가 없습니다.</div>
        <div v-else class="grid grid-cols-2 md:grid-cols-4 gap-6">
          <div v-for="m in metrics" :key="m.key" class="text-center">
            <p class="text-xs text-gray-400 mb-2">{{ m.label }}</p>
            <div class="relative w-24 h-24 mx-auto">
              <svg viewBox="0 0 36 36" class="w-24 h-24 -rotate-90">
                <circle cx="18" cy="18" r="15.9" fill="none" stroke="#e5e7eb" stroke-width="3"/>
                <circle cx="18" cy="18" r="15.9" fill="none" :stroke="m.color" stroke-width="3"
                  :stroke-dasharray="ov.rag_evaluation[m.key] + ' 100'"
                  stroke-linecap="round"/>
              </svg>
              <span class="absolute inset-0 flex items-center justify-center text-xl font-black">
                {{ ov.rag_evaluation[m.key] }}%
              </span>
            </div>
          </div>
        </div>
      </div>

      <div class="grid grid-cols-1 md:grid-cols-2 gap-6 mb-8">
        <!-- 문서별 쿼리 수 -->
        <div class="bg-white rounded-xl shadow p-6">
          <h2 class="text-lg font-bold mb-4">문서별 쿼리 현황</h2>
          <div v-if="!ov.doc_stats.length" class="text-gray-400 text-sm">데이터 없음</div>
          <div v-for="d in ov.doc_stats" :key="d.filename" class="mb-3">
            <div class="flex justify-between text-sm mb-1">
              <span class="truncate max-w-xs text-gray-700">{{ d.filename }}</span>
              <span class="font-semibold text-blue-600">{{ d.query_count }}건</span>
            </div>
            <div class="w-full bg-gray-100 rounded-full h-2">
              <div class="bg-blue-500 h-2 rounded-full" :style="{width: barWidth(d.query_count, maxDocQ) + '%'}"></div>
            </div>
          </div>
        </div>

        <!-- 사용자별 쿼리 수 -->
        <div class="bg-white rounded-xl shadow p-6">
          <h2 class="text-lg font-bold mb-4">사용자 활동량 TOP 10</h2>
          <div v-if="!ov.user_stats.length" class="text-gray-400 text-sm">데이터 없음</div>
          <div v-for="u in ov.user_stats" :key="u.email" class="mb-3">
            <div class="flex justify-between text-sm mb-1">
              <span class="text-gray-700">{{ u.name }} <span class="text-gray-400 text-xs">{{ u.email }}</span></span>
              <span class="font-semibold text-purple-600">{{ u.query_count }}건</span>
            </div>
            <div class="w-full bg-gray-100 rounded-full h-2">
              <div class="bg-purple-500 h-2 rounded-full" :style="{width: barWidth(u.query_count, maxUserQ) + '%'}"></div>
            </div>
          </div>
        </div>
      </div>

      <!-- 최근 쿼리 -->
      <div class="bg-white rounded-xl shadow p-6">
        <h2 class="text-lg font-bold mb-4">최근 RAG 쿼리</h2>
        <div v-if="!ov.recent_queries.length" class="text-gray-400 text-sm">아직 쿼리가 없습니다.</div>
        <table v-else class="w-full text-sm">
          <thead><tr class="text-left text-gray-400 border-b">
            <th class="pb-2 font-medium">쿼리</th>
            <th class="pb-2 font-medium">모델</th>
            <th class="pb-2 font-medium">응답시간</th>
            <th class="pb-2 font-medium">시각</th>
          </tr></thead>
          <tbody>
            <tr v-for="q in ov.recent_queries" :key="q.queried_at" class="border-b last:border-0">
              <td class="py-2 text-gray-700 max-w-xs truncate">{{ q.query }}</td>
              <td class="py-2 text-gray-500">{{ q.model || '-' }}</td>
              <td class="py-2 text-gray-500">{{ q.latency_ms != null ? q.latency_ms + 'ms' : '-' }}</td>
              <td class="py-2 text-gray-400">{{ q.queried_at }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  `,
  setup() {
    const ov = ref({
      summary: { total_users: 0, total_docs: 0, total_queries: 0, total_chunks: 0, quiz_accuracy: 0 },
      model_stats: [],
      rag_evaluation: { faithfulness: 0, answer_relevancy: 0, context_precision: 0, context_recall: 0 },
      recent_queries: [],
      doc_stats: [],
      user_stats: [],
    });

    const metrics = [
      { key: 'faithfulness',      label: '충실도',         color: '#3b82f6' },
      { key: 'answer_relevancy',  label: '답변 관련성',    color: '#10b981' },
      { key: 'context_precision', label: '컨텍스트 정밀도', color: '#f59e0b' },
      { key: 'context_recall',    label: '컨텍스트 재현율', color: '#8b5cf6' },
    ];

    const noEval  = Vue.computed(() => Object.values(ov.value.rag_evaluation).every(v => v === 0));
    const maxDocQ  = Vue.computed(() => Math.max(1, ...ov.value.doc_stats.map(d => d.query_count)));
    const maxUserQ = Vue.computed(() => Math.max(1, ...ov.value.user_stats.map(u => u.query_count)));
    const barWidth = (val, max) => Math.round((val / max) * 100);

    onMounted(async () => {
      try {
        const res = await axios.get(`${API_URL}/api/admin/overview`);
        ov.value = res.data;
      } catch (e) { console.error(e); }
    });

    return { ov, metrics, noEval, maxDocQ, maxUserQ, barWidth };
  }
};

// ─── App Shell ───────────────────────────────
const App = {
  template: `
    <div v-if="loading" class="min-h-screen flex items-center justify-center">
      <div class="animate-spin rounded-full h-12 w-12 border-t-2 border-b-2 border-blue-500"></div>
    </div>
    <div v-else-if="!user">
      <Login />
    </div>
    <div v-else class="flex min-h-screen">
      <nav class="w-64 bg-gray-900 text-white p-6 flex flex-col shadow-2xl">
        <h2 class="text-2xl font-black mb-10 tracking-tighter text-blue-400">RAG STUDY</h2>
        <div class="mb-10 p-4 bg-gray-800 rounded-xl border border-gray-700">
          <p class="text-xs text-gray-400 mb-1">WELCOME BACK</p>
          <p class="font-bold text-lg truncate">{{ user.name }}님</p>
          <span :class="user.role === 'admin' ? 'bg-yellow-500 text-gray-900' : 'bg-blue-600 text-white'"
                class="inline-block text-xs font-semibold px-2 py-0.5 rounded mt-1">
            {{ user.role === 'admin' ? 'ADMIN' : 'USER' }}
          </span>
          <a href="/logout" class="text-xs text-red-400 hover:text-red-300 mt-2 block">로그아웃</a>
        </div>
        <div class="flex flex-col gap-2 flex-1">
          <router-link to="/" class="p-3 rounded-lg hover:bg-gray-800 transition-colors flex items-center gap-3" active-class="bg-blue-600 hover:bg-blue-600">
            <span>대시보드</span>
          </router-link>
          <router-link to="/upload" class="p-3 rounded-lg hover:bg-gray-800 transition-colors flex items-center gap-3" active-class="bg-blue-600 hover:bg-blue-600">
            <span>자료 업로드</span>
          </router-link>
          <router-link to="/chat" class="p-3 rounded-lg hover:bg-gray-800 transition-colors flex items-center gap-3" active-class="bg-blue-600 hover:bg-blue-600">
            <span>챗봇 학습</span>
          </router-link>
          <router-link to="/questions" class="p-3 rounded-lg hover:bg-gray-800 transition-colors flex items-center gap-3" active-class="bg-blue-600 hover:bg-blue-600">
            <span>문제은행</span>
          </router-link>
          <router-link v-if="user.role === 'admin'" to="/admin"
            class="p-3 rounded-lg hover:bg-gray-800 transition-colors flex items-center gap-3 mt-4 border-t border-gray-700 pt-4"
            active-class="bg-yellow-500 hover:bg-yellow-500 text-gray-900">
            <span>관리자 대시보드</span>
          </router-link>
        </div>
      </nav>
      <main class="flex-1 overflow-auto bg-gray-50">
        <router-view></router-view>
      </main>
    </div>
  `,
  setup() {
    const user    = ref(null);
    const loading = ref(true);
    onMounted(async () => {
      user.value    = await checkAuth();
      loading.value = false;
    });
    return { user, loading };
  },
  components: { Login }
};

// ─── Router ──────────────────────────────────
const routes = [
  { path: '/',          component: Dashboard },
  { path: '/upload',    component: Upload },
  { path: '/chat',      component: Chat },
  { path: '/questions', component: Questions },
  { path: '/admin',     component: AdminDashboard },
];

const router = createRouter({ history: createWebHashHistory(), routes });
createApp(App).use(router).mount('#app');
