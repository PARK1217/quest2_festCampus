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

      <!-- 로딩 -->
      <div v-if="loading" class="flex items-center justify-center h-48 text-gray-400 gap-3">
        <span class="animate-spin inline-block w-6 h-6 border-2 border-blue-400 border-t-transparent rounded-full"></span>
        <span>데이터 불러오는 중...</span>
      </div>

      <!-- 에러 -->
      <div v-else-if="error" class="bg-red-50 border border-red-200 rounded-xl p-6 mb-6 text-red-700">
        <p class="font-semibold mb-1">데이터를 불러오지 못했습니다.</p>
        <p class="text-sm font-mono">{{ error }}</p>
        <button @click="$router.go(0)" class="mt-3 text-sm underline hover:no-underline">새로고침</button>
      </div>

      <template v-else>
      <!-- 통계 카드 -->
      <div class="grid grid-cols-2 md:grid-cols-4 gap-6 mb-8">
        <div class="bg-white p-6 rounded-lg shadow-md border-t-4 border-blue-500">
          <h2 class="text-sm font-semibold text-gray-500 mb-2">퀴즈 정답률</h2>
          <div class="text-4xl font-bold text-blue-600">{{ data.stats.correct_rate }}%</div>
          <p class="text-xs text-gray-400 mt-1">{{ data.stats.correct_count }} / {{ data.stats.total_attempts }}문제</p>
        </div>
        <div class="bg-white p-6 rounded-lg shadow-md border-t-4 border-green-500">
          <h2 class="text-sm font-semibold text-gray-500 mb-2">학습 진도</h2>
          <div class="text-4xl font-bold text-green-600">{{ data.stats.progress }}%</div>
        </div>
        <div class="bg-white p-6 rounded-lg shadow-md border-t-4 border-purple-500">
          <h2 class="text-sm font-semibold text-gray-500 mb-2">업로드 문서</h2>
          <div class="text-4xl font-bold text-purple-600">{{ data.stats.total_docs }}</div>
        </div>
        <div class="bg-white p-6 rounded-lg shadow-md border-t-4 border-yellow-500">
          <h2 class="text-sm font-semibold text-gray-500 mb-2">총 질문 수</h2>
          <div class="text-4xl font-bold text-yellow-600">{{ data.stats.total_queries }}</div>
        </div>
      </div>

      <!-- 빈 상태 배너 -->
      <div v-if="data.stats.total_docs === 0" class="mb-8 bg-blue-50 border border-blue-200 rounded-xl p-6 text-center">
        <p class="text-blue-700 font-semibold mb-3">아직 업로드된 문서가 없습니다.</p>
        <router-link to="/upload" class="inline-block bg-blue-600 text-white px-5 py-2 rounded-lg hover:bg-blue-700 transition-colors">자료 업로드하기</router-link>
      </div>

      <!-- 주로 사용한 모델 -->
      <div v-if="data.model_usage.length > 0" class="bg-white rounded-xl shadow p-6 mb-6">
        <h2 class="text-lg font-bold mb-4">주로 사용한 모델</h2>
        <div class="space-y-3">
          <div v-for="m in data.model_usage" :key="m.model">
            <div class="flex justify-between text-sm mb-1">
              <span class="font-semibold uppercase" :class="modelTextColor(m.model)">{{ m.model }}</span>
              <span class="text-gray-500">{{ m.count }}회 ({{ m.pct }}%)</span>
            </div>
            <div class="w-full bg-gray-100 rounded-full h-2.5">
              <div class="h-2.5 rounded-full transition-all" :class="modelBarColor(m.model)" :style="{width: m.pct + '%'}"></div>
            </div>
          </div>
        </div>
      </div>

      <!-- 최근 질문 내용과 답변 -->
      <div v-if="data.recent_queries.length > 0" class="bg-white rounded-xl shadow p-6 mb-6">
        <h2 class="text-lg font-bold mb-4">최근 질문 내용과 답변</h2>
        <div class="space-y-3">
          <div v-for="(q, i) in data.recent_queries" :key="i" class="border border-gray-100 rounded-xl overflow-hidden">
            <div class="flex items-start gap-3 p-4 bg-gray-50 cursor-pointer hover:bg-gray-100 transition-colors" @click="toggleQuery(i)">
              <div class="flex-shrink-0 w-6 h-6 bg-blue-100 text-blue-600 rounded-full flex items-center justify-center text-xs font-bold">Q</div>
              <p class="text-sm font-medium text-gray-800 flex-1">{{ q.query }}</p>
              <div class="flex items-center gap-2 flex-shrink-0">
                <span :class="modelBadge(q.model)">{{ q.model || '-' }}</span>
                <span class="text-gray-400 text-xs hidden md:inline">{{ q.queried_at }}</span>
                <svg class="w-4 h-4 text-gray-400 transition-transform" :class="expandedQuery === i ? 'rotate-180' : ''" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"/>
                </svg>
              </div>
            </div>
            <div v-if="expandedQuery === i" class="p-4 border-t border-gray-100">
              <div class="flex items-start gap-3">
                <div class="flex-shrink-0 w-6 h-6 bg-green-100 text-green-600 rounded-full flex items-center justify-center text-xs font-bold">A</div>
                <p class="text-sm text-gray-700 leading-relaxed whitespace-pre-wrap">{{ q.response }}</p>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- 틀린 문제 복습하기 요약 카드 -->
      <div v-if="data.wrong_answers.length > 0" class="bg-white rounded-xl shadow p-6">
        <div class="flex items-center justify-between mb-4">
          <div>
            <h2 class="text-lg font-bold">오답 복습</h2>
            <p class="text-sm text-gray-400 mt-0.5">틀린 문제 <span class="text-red-500 font-semibold">{{ data.wrong_answers.length }}개</span>가 대기 중입니다.</p>
          </div>
          <router-link to="/review"
            class="bg-red-500 text-white px-5 py-2.5 rounded-xl font-semibold text-sm hover:bg-red-600 transition-colors flex items-center gap-2 shadow-sm">
            복습 시작 →
          </router-link>
        </div>
        <div class="space-y-1.5">
          <div v-for="(w, i) in data.wrong_answers.slice(0, 4)" :key="i"
            class="flex items-center gap-2 px-3 py-2 rounded-lg bg-red-50 border border-red-100 text-sm text-gray-700">
            <span class="text-red-400 font-bold flex-shrink-0">Q{{ i + 1 }}.</span>
            <span class="truncate">{{ w.question }}</span>
          </div>
          <p v-if="data.wrong_answers.length > 4" class="text-xs text-gray-400 text-right pt-1">+ {{ data.wrong_answers.length - 4 }}개 더...</p>
        </div>
      </div>
      </template>
    </div>
  `,
  setup() {
    const data = ref({
      stats: { correct_rate: 0, progress: 0, total_docs: 0, total_queries: 0, total_attempts: 0, correct_count: 0 },
      model_usage: [],
      recent_queries: [],
      wrong_answers: [],
    });
    const expandedQuery = ref(null);

    const toggleQuery = (i) => {
      expandedQuery.value = expandedQuery.value === i ? null : i;
    };

    const MODEL_COLORS = {
      'openai':       { text: 'text-sky-600',     bar: 'bg-sky-500'     },
      'gemini':       { text: 'text-emerald-600', bar: 'bg-emerald-500' },
      'groq':         { text: 'text-violet-600',  bar: 'bg-violet-500'  },
      'groq-70b':     { text: 'text-purple-700',  bar: 'bg-purple-600'  },
      'groq-mixtral': { text: 'text-fuchsia-600', bar: 'bg-fuchsia-500' },
      'groq-gemma':   { text: 'text-orange-600',  bar: 'bg-orange-500'  },
      'huggingface':  { text: 'text-amber-600',   bar: 'bg-amber-500'   },
      'mistral':      { text: 'text-indigo-600',  bar: 'bg-indigo-500'  },
      'cerebras':     { text: 'text-rose-600',    bar: 'bg-rose-500'    },
      'cohere':       { text: 'text-teal-600',    bar: 'bg-teal-500'    },
      'ollama':       { text: 'text-slate-600',   bar: 'bg-slate-500'   },
      'free':         { text: 'text-gray-500',    bar: 'bg-gray-400'    },
    };
    const modelTextColor = (model) => (MODEL_COLORS[model]?.text || 'text-gray-500');
    const modelBarColor  = (model) => (MODEL_COLORS[model]?.bar  || 'bg-gray-400');

    const modelBadge = (model) => {
      const map = {
        'openai':       'bg-sky-100 text-sky-700 border-sky-200',
        'gemini':       'bg-emerald-100 text-emerald-700 border-emerald-200',
        'groq':         'bg-violet-100 text-purple-700 border-purple-200',
        'groq-70b':     'bg-purple-100 text-purple-800 border-purple-300',
        'groq-mixtral': 'bg-fuchsia-100 text-fuchsia-700 border-fuchsia-200',
        'groq-gemma':   'bg-orange-100 text-orange-700 border-orange-200',
        'huggingface':  'bg-amber-100 text-amber-700 border-amber-200',
        'mistral':      'bg-indigo-100 text-indigo-700 border-indigo-200',
        'cerebras':     'bg-rose-100 text-rose-700 border-rose-200',
        'cohere':       'bg-teal-100 text-teal-700 border-teal-200',
        'ollama':       'bg-slate-100 text-slate-700 border-slate-300',
        'free':         'bg-gray-100 text-gray-600 border-gray-200',
      };
      const cls = map[model] || 'bg-gray-50 text-gray-500 border-gray-100';
      return `px-2 py-0.5 rounded-md text-[10px] font-bold border uppercase ${cls}`;
    };

    const loading = ref(true);
    const error   = ref('');

    onMounted(async () => {
      try {
        const res = await axios.get(`${API_URL}/api/dashboard`);
        data.value = res.data;
      } catch (e) {
        console.error('dashboard 로드 실패', e);
        error.value = e.response?.data?.detail || e.message || '알 수 없는 오류';
      } finally {
        loading.value = false;
      }
    });
    return { data, expandedQuery, toggleQuery, modelTextColor, modelBarColor, modelBadge, loading, error };
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
            {{ selectedFiles.length === 0 ? 'PDF 또는 텍스트 파일을 드래그하거나 클릭하세요' : selectedFiles.length + '개 파일 선택됨' }}
          </p>
          <p class="text-gray-400 text-sm mt-1">지원 형식: .pdf, .txt · 여러 파일 동시 선택 가능</p>
          <input ref="fileInput" type="file" accept=".pdf,.txt" multiple class="hidden" @change="onFileChange">
        </div>

        <!-- 선택된 파일 목록 -->
        <div v-if="selectedFiles.length > 0" class="mt-3 flex flex-wrap gap-2">
          <span v-for="(f, i) in selectedFiles" :key="i"
            class="inline-flex items-center gap-1 bg-blue-50 border border-blue-200 text-blue-700 text-xs px-2.5 py-1 rounded-full">
            {{ f.name }}
            <button @click.stop="removeSelectedFile(i)" class="hover:text-red-500 leading-none font-bold">&times;</button>
          </span>
        </div>

        <div class="mt-4 flex items-center gap-4">
          <button
            @click="uploadFiles"
            :disabled="selectedFiles.length === 0 || uploading"
            class="bg-blue-600 text-white px-6 py-2.5 rounded-lg font-semibold hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors flex items-center gap-2"
          >
            <span v-if="uploading" class="animate-spin inline-block w-4 h-4 border-2 border-white border-t-transparent rounded-full"></span>
            {{ uploading ? uploadProgress : '업로드' }}
          </button>
          <p v-if="message" :class="messageOk ? 'text-green-600' : 'text-red-500'" class="font-medium">{{ message }}</p>
        </div>
      </div>

      <!-- 문서 목록 -->
      <div v-if="documents.length > 0" class="bg-white rounded-xl shadow p-6">
        <div class="flex items-center justify-between mb-4">
          <div class="flex items-center gap-3">
            <h2 class="text-lg font-bold">업로드된 문서</h2>
            <span v-if="checkedIds.length > 0" class="text-sm text-blue-600 font-medium">{{ checkedIds.length }}개 선택됨</span>
          </div>
          <div class="flex items-center gap-2">
            <!-- 다중 선택 액션 버튼 -->
            <template v-if="checkedIds.length > 0">
              <button @click="goToChat()" class="text-sm bg-green-600 text-white px-4 py-1.5 rounded-lg hover:bg-green-700 transition-colors font-medium flex items-center gap-1">
                <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z"/></svg>
                채팅하기
              </button>
              <button @click="goToQuestions()" class="text-sm bg-purple-600 text-white px-4 py-1.5 rounded-lg hover:bg-purple-700 transition-colors font-medium flex items-center gap-1">
                <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2"/></svg>
                문제 생성
              </button>
              <button @click="checkedIds = []" class="text-sm text-gray-500 px-3 py-1.5 rounded-lg hover:bg-gray-100 transition-colors">선택 해제</button>
            </template>
            <span v-if="totalPages > 1" class="text-sm text-gray-400">
              전체 {{ documents.length }}개 · {{ currentPage }} / {{ totalPages }} 페이지
            </span>
          </div>
        </div>

        <table class="w-full text-sm">
          <thead>
            <tr class="text-left text-gray-400 border-b">
              <th class="pb-2 w-8">
                <input type="checkbox" class="accent-blue-600" :checked="allPageChecked" @change="toggleAllPage">
              </th>
              <th class="pb-2 font-medium">파일명</th>
              <th class="pb-2 font-medium">형식</th>
              <th class="pb-2 font-medium">업로드 일시</th>
              <th class="pb-2 font-medium">개별 바로가기</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(doc, i) in pagedDocuments" :key="doc.id"
              class="border-b last:border-0 hover:bg-gray-50 transition-colors"
              :class="checkedIds.includes(doc.id) ? 'bg-blue-50' : ''">
              <td class="py-3">
                <input type="checkbox" class="accent-blue-600" :value="doc.id" v-model="checkedIds">
              </td>
              <td class="py-3 text-gray-800 font-medium max-w-xs truncate">{{ doc.filename }}</td>
              <td class="py-3">
                <span class="bg-blue-100 text-blue-700 text-xs font-semibold px-2 py-0.5 rounded uppercase">{{ doc.file_type }}</span>
              </td>
              <td class="py-3 text-gray-500">{{ formatDate(doc.uploaded_at) }}</td>
              <td class="py-3 flex gap-2">
                <button @click="goToChat([doc.id])" class="text-xs bg-green-100 text-green-700 px-3 py-1 rounded-lg hover:bg-green-200 transition-colors font-medium">채팅</button>
                <button @click="goToQuestions([doc.id])" class="text-xs bg-purple-100 text-purple-700 px-3 py-1 rounded-lg hover:bg-purple-200 transition-colors font-medium">문제 생성</button>
                <button @click="deleteDocument(doc.id, doc.filename)" class="text-xs bg-red-100 text-red-600 px-3 py-1 rounded-lg hover:bg-red-200 transition-colors font-medium">삭제</button>
              </td>
            </tr>
          </tbody>
        </table>

        <!-- 페이지네이션 -->
        <div v-if="totalPages > 1" class="flex items-center justify-center gap-1 mt-6">
          <button @click="currentPage = 1" :disabled="currentPage === 1"
            class="px-2 py-1 rounded text-sm text-gray-500 hover:bg-gray-100 disabled:opacity-30 disabled:cursor-not-allowed">«</button>
          <button @click="currentPage--" :disabled="currentPage === 1"
            class="px-3 py-1 rounded text-sm text-gray-500 hover:bg-gray-100 disabled:opacity-30 disabled:cursor-not-allowed">‹</button>
          <button v-for="p in pageNumbers" :key="p"
            @click="typeof p === 'number' && (currentPage = p)"
            :class="p === currentPage ? 'bg-blue-600 text-white font-semibold' : p === '...' ? 'cursor-default text-gray-400' : 'text-gray-600 hover:bg-gray-100'"
            class="w-9 h-9 rounded-lg text-sm transition-colors">{{ p }}</button>
          <button @click="currentPage++" :disabled="currentPage === totalPages"
            class="px-3 py-1 rounded text-sm text-gray-500 hover:bg-gray-100 disabled:opacity-30 disabled:cursor-not-allowed">›</button>
          <button @click="currentPage = totalPages" :disabled="currentPage === totalPages"
            class="px-2 py-1 rounded text-sm text-gray-500 hover:bg-gray-100 disabled:opacity-30 disabled:cursor-not-allowed">»</button>
        </div>
      </div>
    </div>
  `,
  setup() {
    const router       = VueRouter.useRouter();
    const documents    = ref([]);
    const selectedFiles = ref([]);   // 업로드할 파일 배열
    const checkedIds   = ref([]);    // 목록에서 선택된 문서 ID 배열
    const uploading    = ref(false);
    const uploadProgress = ref('');
    const dragOver     = ref(false);
    const message      = ref('');
    const messageOk    = ref(true);
    const currentPage  = ref(1);
    const pageSize     = 20;

    const totalPages    = Vue.computed(() => Math.max(1, Math.ceil(documents.value.length / pageSize)));
    const pagedDocuments = Vue.computed(() => {
      const start = (currentPage.value - 1) * pageSize;
      return documents.value.slice(start, start + pageSize);
    });
    const allPageChecked = Vue.computed(() =>
      pagedDocuments.value.length > 0 && pagedDocuments.value.every(d => checkedIds.value.includes(d.id))
    );
    const pageNumbers = Vue.computed(() => {
      const total = totalPages.value, cur = currentPage.value;
      if (total <= 7) return Array.from({ length: total }, (_, i) => i + 1);
      if (cur <= 4)       return [1, 2, 3, 4, 5, '...', total];
      if (cur >= total-3) return [1, '...', total-4, total-3, total-2, total-1, total];
      return [1, '...', cur-1, cur, cur+1, '...', total];
    });

    const fetchDocuments = async () => {
      try {
        documents.value = (await axios.get(`${API_URL}/api/documents`)).data;
        currentPage.value = 1;
        checkedIds.value  = [];
      } catch (e) { console.error(e); }
    };

    const toggleAllPage = (e) => {
      const pageIds = pagedDocuments.value.map(d => d.id);
      if (e.target.checked) {
        checkedIds.value = [...new Set([...checkedIds.value, ...pageIds])];
      } else {
        checkedIds.value = checkedIds.value.filter(id => !pageIds.includes(id));
      }
    };

    const onFileChange = (e) => {
      selectedFiles.value = Array.from(e.target.files);
    };
    const onDrop = (e) => {
      dragOver.value = false;
      selectedFiles.value = Array.from(e.dataTransfer.files).filter(f => f.name.match(/\.(pdf|txt)$/i));
    };
    const removeSelectedFile = (i) => {
      selectedFiles.value = selectedFiles.value.filter((_, idx) => idx !== i);
    };

    const uploadFiles = async () => {
      if (selectedFiles.value.length === 0 || uploading.value) return;
      uploading.value      = true;
      message.value        = '';
      uploadProgress.value = `업로드 중... (${selectedFiles.value.length}개)`;

      const formData = new FormData();
      for (const file of selectedFiles.value) {
        formData.append('files', file);
      }

      try {
        const res = await axios.post(`${API_URL}/api/upload`, formData);
        messageOk.value     = true;
        message.value       = res.data.message;
        selectedFiles.value = [];
      } catch (e) {
        messageOk.value = false;
        message.value   = '업로드 실패: ' + (e.response?.data?.detail || e.message);
      } finally {
        uploading.value      = false;
        uploadProgress.value = '';
        await fetchDocuments();
      }
    };

    const deleteDocument = async (id, filename) => {
      if (!confirm(`"${filename}" 문서를 삭제하시겠습니까?\n(대화·문제 기록은 유지되며 해당 문서로의 검색만 비활성화됩니다.)`)) return;
      try {
        await axios.delete(`${API_URL}/api/documents/${id}`);
        await fetchDocuments();
      } catch (e) {
        alert('삭제 실패: ' + (e.response?.data?.detail || e.message));
      }
    };

    const formatDate = (iso) => {
      if (!iso) return '-';
      return new Date(iso).toLocaleString('ko-KR', { year:'numeric', month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit' });
    };

    const buildDocQuery = (ids) => {
      const selected = ids || checkedIds.value;
      return selected.length === 1
        ? { document_id: selected[0] }
        : { document_ids: selected.join(',') };
    };
    const goToChat      = (ids) => router.push({ path: '/chat',      query: buildDocQuery(ids) });
    const goToQuestions = (ids) => router.push({ path: '/questions', query: buildDocQuery(ids) });

    onMounted(fetchDocuments);
    return {
      documents, selectedFiles, checkedIds, uploading, uploadProgress, dragOver, message, messageOk,
      currentPage, pageSize, totalPages, pagedDocuments, allPageChecked, pageNumbers,
      onFileChange, onDrop, removeSelectedFile, uploadFiles, deleteDocument,
      formatDate, goToChat, goToQuestions, toggleAllPage,
    };
  }
};

// ─── Chat ────────────────────────────────────
const Chat = {
  template: `
    <div class="flex flex-col h-screen">
      <!-- 에러 팝업 -->
      <div v-if="errorPopup.show" class="fixed inset-0 z-50 flex items-center justify-center bg-black/40" @click.self="errorPopup.show = false">
        <div class="bg-white rounded-2xl shadow-2xl max-w-md w-full mx-4 overflow-hidden">
          <div :class="{
            'bg-yellow-500': errorPopup.code === 'rate_limit',
            'bg-red-500':    errorPopup.code === 'not_found' || errorPopup.code === 'bad_request',
            'bg-gray-700':   errorPopup.code === 'error'
          }" class="px-6 py-4 flex items-center gap-3">
            <span class="text-2xl">{{ errorPopup.code === 'rate_limit' ? '⏳' : errorPopup.code === 'not_found' ? '🔍' : errorPopup.code === 'bad_request' ? '⚠️' : '❌' }}</span>
            <span class="text-white font-bold text-lg">{{ errorPopup.code === 'rate_limit' ? '사용 한도 초과' : errorPopup.code === 'not_found' ? '모델을 찾을 수 없음' : errorPopup.code === 'bad_request' ? '요청 오류' : '오류 발생' }}</span>
          </div>
          <div class="px-6 py-5">
            <p class="text-gray-700 text-sm whitespace-pre-wrap">{{ errorPopup.message }}</p>
          </div>
          <div class="px-6 pb-5 flex justify-end">
            <button @click="errorPopup.show = false" class="bg-gray-100 hover:bg-gray-200 text-gray-700 font-semibold px-5 py-2 rounded-lg transition-colors text-sm">닫기</button>
          </div>
        </div>
      </div>

      <!-- 문서 드롭다운 backdrop -->
      <div v-if="docSelectorOpen" class="fixed inset-0 z-20" @click="docSelectorOpen = false"></div>

      <!-- 헤더 -->
      <div class="bg-white border-b px-6 py-4 shadow-sm">
        <div class="flex flex-wrap items-center gap-4">
          <h1 class="text-xl font-bold">챗봇 학습</h1>
          <div class="relative flex items-center gap-2">
            <span class="text-sm font-medium text-gray-500">문서:</span>
            <div class="relative z-30">
              <button @click="docSelectorOpen = !docSelectorOpen"
                class="border border-gray-300 rounded-lg px-3 py-2 text-sm bg-white flex items-center gap-2 min-w-[180px] justify-between hover:border-blue-400 transition-colors">
                <span class="truncate">{{ selectedDocIds.length === 0 ? '문서를 선택하세요' : selectedDocIds.length + '개 문서 선택됨' }}</span>
                <svg class="w-4 h-4 text-gray-400 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"/></svg>
              </button>
              <div v-if="docSelectorOpen" class="absolute top-full left-0 mt-1 w-72 bg-white border border-gray-200 rounded-xl shadow-xl z-30 overflow-hidden">
                <div class="px-3 py-2 border-b bg-gray-50 flex items-center justify-between">
                  <span class="text-xs font-semibold text-gray-500">문서 선택 (복수 가능)</span>
                  <button @click="selectedDocIds = []" class="text-xs text-gray-400 hover:text-red-500">전체 해제</button>
                </div>
                <div class="max-h-56 overflow-y-auto">
                  <label v-for="doc in documents" :key="doc.id"
                    class="flex items-center gap-3 px-4 py-2.5 hover:bg-blue-50 cursor-pointer transition-colors"
                    :class="selectedDocIds.includes(doc.id) ? 'bg-blue-50' : ''">
                    <input type="checkbox" :value="doc.id" v-model="selectedDocIds" class="accent-blue-600 w-4 h-4 flex-shrink-0">
                    <span class="text-sm truncate" :title="doc.filename">{{ doc.filename }}</span>
                  </label>
                  <div v-if="documents.length === 0" class="px-4 py-3 text-sm text-gray-400 text-center">문서가 없습니다</div>
                </div>
              </div>
            </div>
          </div>
          <div class="flex items-center gap-2">
            <span class="text-sm font-medium text-gray-500">모델:</span>
            <select
              v-model="selectedProvider"
              class="border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            >
              <option v-for="m in availableModels" :key="m.id" :value="m.id">{{ m.name }}{{ m.badge ? ' [' + m.badge + ']' : '' }}</option>
            </select>
          </div>
          <router-link v-if="documents.length === 0" to="/upload" class="text-blue-600 text-sm hover:underline">파일 업로드하기 →</router-link>
        </div>
        <p v-if="selectedModelBadge" class="mt-1.5 text-[11px] text-amber-600 flex items-center gap-1">
          <span>⚠</span>
          <span v-if="selectedModelBadge === '무료'">무료 모델은 API 호출 한도 초과 시 응답이 제한될 수 있습니다.</span>
          <span v-else-if="selectedModelBadge === '무료티어'">무료 티어 모델은 월별 크레딧 소진 시 동작하지 않을 수 있습니다.</span>
        </p>
        <!-- 선택된 문서 태그 표시 -->
        <div v-if="selectedDocIds.length > 0" class="mt-2 flex flex-wrap gap-1.5">
          <span v-for="id in selectedDocIds" :key="id"
            class="inline-flex items-center gap-1 bg-blue-100 text-blue-700 text-xs font-medium px-2.5 py-1 rounded-full">
            {{ documents.find(d => d.id === id)?.filename }}
            <button @click="selectedDocIds = selectedDocIds.filter(x => x !== id)" class="hover:text-blue-900 leading-none">&times;</button>
          </span>
        </div>
      </div>

      <!-- 메시지 영역 -->
      <div ref="msgBox" class="flex-1 overflow-y-auto p-6 space-y-4 bg-gray-50">
        <div v-if="selectedDocIds.length === 0 && documents.length === 0" class="flex flex-col items-center justify-center h-full text-center">
          <svg class="w-16 h-16 text-gray-300 mb-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"/></svg>
          <p class="text-gray-500 font-medium">파일을 먼저 업로드해주세요.</p>
          <router-link to="/upload" class="mt-3 text-blue-600 hover:underline text-sm">업로드 페이지 이동 →</router-link>
        </div>
        <div v-else-if="selectedDocIds.length === 0" class="flex items-center justify-center h-full">
          <p class="text-gray-400">위에서 문서를 하나 이상 선택하면 채팅을 시작할 수 있습니다.</p>
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
            :disabled="selectedDocIds.length === 0 || loading"
            type="text"
            placeholder="질문을 입력하세요... (Enter로 전송)"
            class="flex-1 border border-gray-300 rounded-xl px-4 py-3 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:bg-gray-100"
          >
          <button
            @click="sendMessage"
            :disabled="selectedDocIds.length === 0 || !inputText.trim() || loading"
            class="bg-blue-600 text-white px-6 py-3 rounded-xl font-semibold hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >전송</button>
        </div>
      </div>
    </div>
  `,
  setup() {
    const route            = VueRouter.useRoute();
    const documents        = ref([]);
    const selectedDocIds   = ref([]);      // 다중 선택
    const docSelectorOpen  = ref(false);
    const selectedProvider = ref('openai');
    const availableModels  = ref([]);
    const messages         = ref([]);
    const errorPopup       = Vue.ref({ show: false, code: '', message: '' });
    const inputText        = ref('');
    const loading          = ref(false);
    const msgBox           = ref(null);

    const scrollBottom = () => nextTick(() => {
      if (msgBox.value) msgBox.value.scrollTop = msgBox.value.scrollHeight;
    });

    const fetchHistory = async (docIds) => {
      if (!docIds || docIds.length === 0) { messages.value = []; return; }
      try {
        const res = await axios.get(`${API_URL}/api/chat/history`, {
          params: { document_ids: docIds.join(',') }
        });
        messages.value = res.data;
        scrollBottom();
      } catch (e) { console.error('대화 내역 로드 실패', e); }
    };

    watch(selectedDocIds, (newIds) => {
      messages.value = [];
      fetchHistory(newIds);
    });

    const sendMessage = async () => {
      if (!inputText.value.trim() || selectedDocIds.value.length === 0 || loading.value) return;
      const query = inputText.value.trim();
      messages.value.push({ role: 'user', content: query });
      inputText.value = '';
      loading.value   = true;
      docSelectorOpen.value = false;
      scrollBottom();
      try {
        const res = await axios.get(`${API_URL}/api/chat`, {
          params: { query, document_ids: selectedDocIds.value.join(','), provider: selectedProvider.value },
        });
        messages.value.push({
          role: 'assistant',
          content: res.data.response,
          model: res.data.model,
          sources: res.data.sources
        });
      } catch (e) {
        const detail  = e.response?.data?.detail;
        const code    = detail?.error_code || 'error';
        const message = detail?.message    || detail || e.message || '알 수 없는 오류가 발생했습니다.';
        errorPopup.value = { show: true, code, message };
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

      if (route.query.document_ids) {
        selectedDocIds.value = route.query.document_ids.split(',').map(Number);
      } else if (route.query.document_id) {
        selectedDocIds.value = [parseInt(route.query.document_id)];
      }
    });

    const selectedModelBadge = Vue.computed(() => {
      const m = availableModels.value.find(m => m.id === selectedProvider.value);
      return m?.badge || '';
    });

    return {
      documents, selectedDocIds, docSelectorOpen, selectedProvider, availableModels,
      messages, inputText, loading, msgBox, sendMessage, selectedModelBadge, errorPopup,
    };
  }
};

// ─── Questions ───────────────────────────────
const Questions = {
  template: `
    <div class="p-8">
      <h1 class="text-3xl font-bold mb-6">문제은행</h1>

      <!-- 문서 드롭다운 backdrop -->
      <div v-if="docSelectorOpen" class="fixed inset-0 z-20" @click="docSelectorOpen = false"></div>

      <div class="bg-white rounded-xl shadow p-6 mb-6">
        <div class="flex flex-wrap items-center gap-4">
          <div class="relative flex items-center gap-2">
            <span class="text-sm font-medium text-gray-500">문서:</span>
            <div class="relative z-30">
              <button @click="docSelectorOpen = !docSelectorOpen"
                class="border border-gray-300 rounded-lg px-3 py-2 text-sm bg-white flex items-center gap-2 min-w-[180px] justify-between hover:border-purple-400 transition-colors">
                <span class="truncate">{{ selectedDocIds.length === 0 ? '문서를 선택하세요' : selectedDocIds.length + '개 문서 선택됨' }}</span>
                <svg class="w-4 h-4 text-gray-400 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"/></svg>
              </button>
              <div v-if="docSelectorOpen" class="absolute top-full left-0 mt-1 w-72 bg-white border border-gray-200 rounded-xl shadow-xl z-30 overflow-hidden">
                <div class="px-3 py-2 border-b bg-gray-50 flex items-center justify-between">
                  <span class="text-xs font-semibold text-gray-500">문서 선택 (복수 가능)</span>
                  <button @click="selectedDocIds = []" class="text-xs text-gray-400 hover:text-red-500">전체 해제</button>
                </div>
                <div class="max-h-56 overflow-y-auto">
                  <label v-for="doc in documents" :key="doc.id"
                    class="flex items-center gap-3 px-4 py-2.5 hover:bg-purple-50 cursor-pointer transition-colors"
                    :class="selectedDocIds.includes(doc.id) ? 'bg-purple-50' : ''">
                    <input type="checkbox" :value="doc.id" v-model="selectedDocIds" class="accent-purple-600 w-4 h-4 flex-shrink-0">
                    <span class="text-sm truncate" :title="doc.filename">{{ doc.filename }}</span>
                  </label>
                  <div v-if="documents.length === 0" class="px-4 py-3 text-sm text-gray-400 text-center">문서가 없습니다</div>
                </div>
              </div>
            </div>
          </div>
          <div class="flex items-center gap-2">
            <span class="text-sm font-medium text-gray-500">모델:</span>
            <select
              v-model="selectedProvider"
              class="border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-purple-500"
            >
              <option v-for="m in availableModels" :key="m.id" :value="m.id">{{ m.name }}{{ m.badge ? ' [' + m.badge + ']' : '' }}</option>
            </select>
          </div>
          <button
            @click="generateQuestions"
            :disabled="selectedDocIds.length === 0 || generating"
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
        <p v-if="selectedModelBadge" class="mt-1.5 text-[11px] text-amber-600 flex items-center gap-1">
          <span>⚠</span>
          <span v-if="selectedModelBadge === '무료'">무료 모델은 API 호출 한도 초과 시 응답이 제한될 수 있습니다.</span>
          <span v-else-if="selectedModelBadge === '무료티어'">무료 티어 모델은 월별 크레딧 소진 시 동작하지 않을 수 있습니다.</span>
        </p>
      </div>

      <!-- 점수 배너 -->
      <div v-if="submitted" class="mb-6 p-5 rounded-xl text-center font-bold text-lg"
           :class="score.correct / score.total >= 0.7 ? 'bg-green-100 text-green-700' : 'bg-orange-100 text-orange-700'">
        {{ score.total }}문제 중 {{ score.correct }}문제 정답 ({{ Math.round(score.correct / score.total * 100) }}%)
        <button @click="resetQuiz" class="ml-4 text-sm font-normal underline">다시 풀기</button>
      </div>

      <!-- 문제 없음 안내 -->
      <div v-if="!generating && questions.length === 0 && selectedDocIds.length > 0" class="bg-gray-50 rounded-xl p-8 text-center text-gray-400">
        선택한 문서의 문제가 없습니다. "문제 생성하기"를 클릭하세요.
      </div>
      <div v-if="selectedDocIds.length === 0" class="bg-gray-50 rounded-xl p-8 text-center text-gray-400">
        문서를 하나 이상 선택하면 문제를 불러오거나 생성할 수 있습니다.
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
    const route            = VueRouter.useRoute();
    const documents        = ref([]);
    const selectedDocIds   = ref([]);
    const docSelectorOpen  = ref(false);
    const selectedProvider = ref('openai');
    const availableModels  = ref([]);
    const questions        = ref([]);
    const userAnswers      = ref({});
    const results          = ref({});
    const generating       = ref(false);
    const submitting       = ref(false);
    const submitted        = ref(false);
    const score            = ref({ correct: 0, total: 0 });
    const reviewMode       = ref(false);

    const hasWrongQuestions  = Vue.computed(() => questions.value.some(q => q.status === 'wrong'));
    const displayedQuestions = Vue.computed(() =>
      reviewMode.value ? questions.value.filter(q => q.status === 'wrong') : questions.value
    );

    const fetchQuestions = async (docIds) => {
      if (!docIds || docIds.length === 0) { questions.value = []; return; }
      try {
        const params = {};
        if (docIds.length === 1) params.document_id = docIds[0];
        const res = await axios.get(`${API_URL}/api/questions`, { params });
        questions.value = res.data;
      } catch (e) { console.error(e); }
    };

    watch(selectedDocIds, async (newIds) => {
      resetQuiz();
      await fetchQuestions(newIds);
    });

    const generateQuestions = async () => {
      if (selectedDocIds.value.length === 0 || generating.value) return;
      generating.value = true;
      resetQuiz();
      try {
        await axios.post(`${API_URL}/api/questions/generate`,
          { document_ids: selectedDocIds.value },
          { params: { provider: selectedProvider.value } }
        );
        await fetchQuestions(selectedDocIds.value);
      } catch (e) {
        alert('문제 생성 실패: ' + (e.response?.data?.detail || e.message));
      } finally {
        generating.value = false;
      }
    };

    const clearQuestions = async () => {
      if (selectedDocIds.value.length === 0) return;
      if (!confirm('선택한 문서와 관련된 모든 문제를 삭제하시겠습니까? (풀이 기록도 함께 삭제됩니다.)')) return;
      try {
        for (const docId of selectedDocIds.value) {
          await axios.delete(`${API_URL}/api/questions`, { params: { document_id: docId } });
        }
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
            question_id: q.id, user_answer: answer,
          });
          resultMap[q.id] = res.data;
          if (res.data.is_correct) correct++;
        } catch (e) {
          resultMap[q.id] = { is_correct: false, correct_answer: '' };
        }
      }
      results.value    = resultMap;
      score.value      = { correct, total: questions.value.length };
      submitted.value  = true;
      submitting.value = false;
      await fetchQuestions(selectedDocIds.value);
    };

    const resetQuiz = () => {
      userAnswers.value = {};
      results.value     = {};
      submitted.value   = false;
      score.value       = { correct: 0, total: 0 };
    };

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

      if (route.query.document_ids) {
        selectedDocIds.value = route.query.document_ids.split(',').map(Number);
      } else if (route.query.document_id) {
        selectedDocIds.value = [parseInt(route.query.document_id)];
      }
    });

    const selectedModelBadge = Vue.computed(() => {
      const m = availableModels.value.find(m => m.id === selectedProvider.value);
      return m?.badge || '';
    });

    return {
      documents, selectedDocIds, docSelectorOpen, selectedProvider, availableModels,
      questions, userAnswers, results, generating, submitting, submitted, score,
      reviewMode, hasWrongQuestions, displayedQuestions,
      generateQuestions, clearQuestions, submitAnswers, resetQuiz, choiceClass, selectedModelBadge,
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
                <td class="py-3">
                  <span :class="modelBadge(ms.model)">{{ ms.model || '-' }}</span>
                </td>
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

      <!-- 사용자 등급 관리 -->
      <div class="bg-white rounded-xl shadow p-6 mb-8">
        <h2 class="text-lg font-bold mb-4">사용자 등급 관리</h2>
        <div v-if="!users.length" class="text-gray-400 text-sm">사용자가 없습니다.</div>
        <table v-else class="w-full text-sm">
          <thead>
            <tr class="text-left text-gray-400 border-b">
              <th class="pb-2 font-medium">이름</th>
              <th class="pb-2 font-medium">이메일</th>
              <th class="pb-2 font-medium text-center">현재 등급</th>
              <th class="pb-2 font-medium text-center">변경</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="u in users" :key="u.id" class="border-b last:border-0 hover:bg-gray-50">
              <td class="py-3 font-medium text-gray-800">{{ u.name }}</td>
              <td class="py-3 text-gray-500 text-xs">{{ u.email }}</td>
              <td class="py-3 text-center">
                <span
                  :class="u.role === 'admin' ? 'bg-yellow-100 text-yellow-700 border border-yellow-300' : 'bg-blue-100 text-blue-700 border border-blue-200'"
                  class="text-xs font-bold px-2.5 py-1 rounded-full"
                >{{ u.role === 'admin' ? 'ADMIN' : 'USER' }}</span>
              </td>
              <td class="py-3 text-center">
                <div class="flex items-center justify-center gap-2">
                  <select
                    v-model="roleSelections[u.id]"
                    class="border border-gray-300 rounded-lg px-2 py-1.5 text-xs focus:outline-none focus:ring-2 focus:ring-yellow-400"
                  >
                    <option value="user">USER</option>
                    <option value="admin">ADMIN</option>
                  </select>
                  <button
                    @click="changeRole(u)"
                    :disabled="roleSelections[u.id] === u.role || changingId === u.id"
                    class="text-xs px-3 py-1.5 rounded-lg font-semibold transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
                    :class="roleSelections[u.id] !== u.role ? 'bg-yellow-500 hover:bg-yellow-600 text-white' : 'bg-gray-100 text-gray-400'"
                  >
                    <span v-if="changingId === u.id" class="animate-spin inline-block w-3 h-3 border-2 border-white border-t-transparent rounded-full"></span>
                    <span v-else>적용</span>
                  </button>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
        <p v-if="roleMsg" class="mt-3 text-sm" :class="roleMsgOk ? 'text-green-600' : 'text-red-500'">{{ roleMsg }}</p>
      </div>

      <!-- 최근 쿼리 미리보기 -->
      <div class="bg-white rounded-xl shadow p-6">
        <div class="flex items-center justify-between mb-4">
          <h2 class="text-lg font-bold">최근 RAG 쿼리</h2>
          <router-link to="/admin/logs" class="text-sm text-blue-600 hover:underline font-medium">전체 로그 보기 →</router-link>
        </div>
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
              <td class="py-2">
                <span :class="modelBadge(q.model)">{{ q.model || '-' }}</span>
              </td>
              <td class="py-2 text-gray-500 text-xs">{{ q.latency_ms != null ? q.latency_ms + 'ms' : '-' }}</td>
              <td class="py-2 text-gray-400 text-[10px]">{{ q.queried_at }}</td>
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

    const users          = ref([]);
    const roleSelections = ref({});
    const changingId     = ref(null);
    const roleMsg        = ref('');
    const roleMsgOk      = ref(true);

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

    const modelBadge = (model) => {
      const map = {
        'openai':       'bg-sky-100 text-sky-700 border-sky-200',
        'gemini':       'bg-emerald-100 text-emerald-700 border-emerald-200',
        'groq':         'bg-violet-100 text-purple-700 border-purple-200',
        'groq-70b':     'bg-purple-100 text-purple-800 border-purple-300',
        'groq-mixtral': 'bg-fuchsia-100 text-fuchsia-700 border-fuchsia-200',
        'groq-gemma':   'bg-orange-100 text-orange-700 border-orange-200',
        'huggingface':  'bg-amber-100 text-amber-700 border-amber-200',
        'mistral':      'bg-indigo-100 text-indigo-700 border-indigo-200',
        'cerebras':     'bg-rose-100 text-rose-700 border-rose-200',
        'cohere':       'bg-teal-100 text-teal-700 border-teal-200',
        'ollama':       'bg-slate-100 text-slate-700 border-slate-300',
        'free':         'bg-gray-100 text-gray-600 border-gray-200',
      };
      const cls = map[model] || 'bg-gray-50 text-gray-500 border-gray-100';
      return `px-2 py-0.5 rounded-md text-[10px] font-bold border uppercase ${cls}`;
    };

    const fetchUsers = async () => {
      const res  = await axios.get(`${API_URL}/api/admin/users`, { params: { limit: 100 } });
      const list = res.data.items || res.data;
      users.value = list;
      // 현재 역할로 선택값 초기화
      list.forEach(u => { roleSelections.value[u.id] = u.role; });
    };

    const changeRole = async (u) => {
      const newRole = roleSelections.value[u.id];
      if (newRole === u.role) return;
      if (!confirm(`"${u.name}"의 등급을 ${u.role.toUpperCase()} → ${newRole.toUpperCase()}으로 변경하시겠습니까?`)) return;
      changingId.value = u.id;
      roleMsg.value    = '';
      try {
        await axios.put(`${API_URL}/api/admin/users/${u.id}/role`, null, { params: { role: newRole } });
        roleMsgOk.value = true;
        roleMsg.value   = `"${u.name}" 등급이 ${newRole.toUpperCase()}으로 변경되었습니다.`;
        await fetchUsers();
      } catch (e) {
        roleMsgOk.value = false;
        roleMsg.value   = '변경 실패: ' + (e.response?.data?.detail || e.message);
      } finally {
        changingId.value = null;
      }
    };

    onMounted(async () => {
      try {
        const [ovRes] = await Promise.all([
          axios.get(`${API_URL}/api/admin/overview`),
          fetchUsers(),
        ]);
        ov.value = ovRes.data;
      } catch (e) { console.error(e); }
    });

    return { 
      ov, metrics, noEval, maxDocQ, maxUserQ, barWidth, modelBadge,
      users, roleSelections, changingId, roleMsg, roleMsgOk, changeRole 
    };
  }
};

// ─── QueryLogs ────────────────────────────────
const QueryLogs = {
  template: `
    <div class="p-8">
      <div class="flex items-center justify-between mb-6">
        <div>
          <h1 class="text-3xl font-bold">RAG 쿼리 로그</h1>
          <p class="text-sm text-gray-400 mt-1">전체 {{ total }}건{{ isFiltered ? ' 중 필터 적용됨' : '' }}</p>
        </div>
        <router-link to="/admin" class="text-sm text-gray-500 hover:text-gray-700 flex items-center gap-1">
          ← 관리자 대시보드
        </router-link>
      </div>

      <!-- 필터 바 -->
      <div class="bg-white rounded-xl shadow p-4 mb-6 flex flex-wrap gap-3 items-end">
        <!-- 검색 -->
        <div class="flex-1 min-w-48">
          <label class="block text-xs font-semibold text-gray-500 mb-1">쿼리 검색</label>
          <div class="relative">
            <svg class="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"/>
            </svg>
            <input
              v-model="filters.search"
              @keydown.enter="applyFilters"
              type="text"
              placeholder="질문 내용으로 검색..."
              class="w-full border border-gray-300 rounded-lg pl-9 pr-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            >
          </div>
        </div>

        <!-- 모델 필터 -->
        <div>
          <label class="block text-xs font-semibold text-gray-500 mb-1">모델</label>
          <select v-model="filters.model" class="border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500">
            <option value="">전체 모델</option>
            <option v-for="m in modelOptions" :key="m" :value="m">{{ m }}</option>
          </select>
        </div>

        <!-- 사용자 필터 -->
        <div>
          <label class="block text-xs font-semibold text-gray-500 mb-1">사용자</label>
          <select v-model="filters.user_id" class="border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500">
            <option value="">전체 사용자</option>
            <option v-for="u in userOptions" :key="u.id" :value="u.id">{{ u.name }}</option>
          </select>
        </div>

        <!-- 적용·초기화 -->
        <div class="flex gap-2">
          <button @click="applyFilters" class="bg-blue-600 text-white px-4 py-2 rounded-lg text-sm font-semibold hover:bg-blue-700 transition-colors">검색</button>
          <button @click="resetFilters" class="bg-gray-100 text-gray-600 px-4 py-2 rounded-lg text-sm font-semibold hover:bg-gray-200 transition-colors">초기화</button>
        </div>
      </div>

      <!-- 로딩 -->
      <div v-if="loading" class="flex justify-center py-16">
        <div class="animate-spin rounded-full h-10 w-10 border-t-2 border-b-2 border-blue-500"></div>
      </div>

      <!-- 결과 없음 -->
      <div v-else-if="items.length === 0" class="bg-white rounded-xl shadow p-12 text-center text-gray-400">
        조건에 맞는 쿼리가 없습니다.
      </div>

      <!-- 로그 테이블 -->
      <div v-else class="bg-white rounded-xl shadow overflow-hidden mb-6">
        <table class="w-full text-sm">
          <thead class="bg-gray-50 border-b">
            <tr class="text-left text-gray-500">
              <th class="px-4 py-3 font-semibold w-8">#</th>
              <th class="px-4 py-3 font-semibold">쿼리</th>
              <th class="px-4 py-3 font-semibold">모델</th>
              <th class="px-4 py-3 font-semibold">사용자</th>
              <th class="px-4 py-3 font-semibold">문서</th>
              <th class="px-4 py-3 font-semibold text-right">토큰</th>
              <th class="px-4 py-3 font-semibold text-right">응답시간</th>
              <th class="px-4 py-3 font-semibold text-right">시각</th>
            </tr>
          </thead>
          <tbody>
            <template v-for="(item, i) in items" :key="item.id">
              <!-- 메인 행 -->
              <tr
                @click="toggleRow(item.id)"
                class="border-b hover:bg-blue-50 cursor-pointer transition-colors"
                :class="expandedId === item.id ? 'bg-blue-50' : ''"
              >
                <td class="px-4 py-3 text-gray-400 text-xs">{{ (currentPage-1)*pageSize + i + 1 }}</td>
                <td class="px-4 py-3 max-w-xs">
                  <p class="truncate font-medium text-gray-800">{{ item.query }}</p>
                </td>
                <td class="px-4 py-3">
                  <span class="text-[11px] font-bold px-2 py-0.5 rounded-full uppercase"
                    :class="modelBadge(item.model)">{{ item.model || '-' }}</span>
                </td>
                <td class="px-4 py-3">
                  <p class="text-gray-700 font-medium">{{ item.user_name }}</p>
                  <p class="text-gray-400 text-[11px]">{{ item.user_email }}</p>
                </td>
                <td class="px-4 py-3 text-gray-500 max-w-[140px] truncate text-xs">{{ item.document_name }}</td>
                <td class="px-4 py-3 text-right">
                  <template v-if="item.total_tokens != null">
                    <p class="font-semibold text-gray-700 text-xs">{{ item.total_tokens.toLocaleString() }}</p>
                    <p class="text-[10px] text-gray-400">↑{{ item.input_tokens }} ↓{{ item.output_tokens }}</p>
                  </template>
                  <span v-else class="text-gray-300 text-xs">-</span>
                </td>
                <td class="px-4 py-3 text-right">
                  <span :class="item.latency_ms > 5000 ? 'text-red-500' : item.latency_ms > 2000 ? 'text-yellow-600' : 'text-green-600'"
                        class="font-semibold text-xs">
                    {{ item.latency_ms != null ? item.latency_ms + 'ms' : '-' }}
                  </span>
                </td>
                <td class="px-4 py-3 text-right text-gray-400 text-xs whitespace-nowrap">{{ item.queried_at }}</td>
              </tr>
              <!-- 펼침 상세 -->
              <tr v-if="expandedId === item.id" class="bg-blue-50/60 border-b">
                <td colspan="7" class="px-6 py-4">
                  <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <div>
                      <p class="text-xs font-bold text-blue-600 uppercase mb-1">질문 (Query)</p>
                      <p class="text-sm text-gray-800 leading-relaxed bg-white rounded-lg p-3 border border-blue-100 whitespace-pre-wrap">{{ item.query }}</p>
                    </div>
                    <div>
                      <p class="text-xs font-bold text-green-600 uppercase mb-1">답변 (Response)</p>
                      <p class="text-sm text-gray-700 leading-relaxed bg-white rounded-lg p-3 border border-green-100 whitespace-pre-wrap max-h-48 overflow-y-auto">{{ item.response || '(응답 없음)' }}</p>
                    </div>
                  </div>
                </td>
              </tr>
            </template>
          </tbody>
        </table>
      </div>

      <!-- 페이지네이션 -->
      <div v-if="totalPages > 1" class="flex items-center justify-center gap-1">
        <button @click="goPage(1)" :disabled="currentPage===1" class="px-2 py-1 rounded text-sm text-gray-500 hover:bg-gray-100 disabled:opacity-30">«</button>
        <button @click="goPage(currentPage-1)" :disabled="currentPage===1" class="px-3 py-1 rounded text-sm text-gray-500 hover:bg-gray-100 disabled:opacity-30">‹</button>
        <button
          v-for="p in pageNumbers" :key="p"
          @click="typeof p === 'number' && goPage(p)"
          :class="p === currentPage ? 'bg-blue-600 text-white font-semibold' : p === '...' ? 'cursor-default text-gray-400' : 'text-gray-600 hover:bg-gray-100'"
          class="w-9 h-9 rounded-lg text-sm transition-colors"
        >{{ p }}</button>
        <button @click="goPage(currentPage+1)" :disabled="currentPage===totalPages" class="px-3 py-1 rounded text-sm text-gray-500 hover:bg-gray-100 disabled:opacity-30">›</button>
        <button @click="goPage(totalPages)" :disabled="currentPage===totalPages" class="px-2 py-1 rounded text-sm text-gray-500 hover:bg-gray-100 disabled:opacity-30">»</button>
      </div>
    </div>
  `,
  setup() {
    const items       = ref([]);
    const total       = ref(0);
    const loading     = ref(false);
    const expandedId  = ref(null);
    const currentPage = ref(1);
    const pageSize    = 20;

    const filters = ref({ search: '', model: '', user_id: '' });
    const modelOptions = ref([]);
    const userOptions  = ref([]);

    const totalPages = Vue.computed(() => Math.ceil(total.value / pageSize));
    const isFiltered = Vue.computed(() => !!(filters.value.search || filters.value.model || filters.value.user_id));

    const pageNumbers = Vue.computed(() => {
      const t = totalPages.value, c = currentPage.value;
      if (t <= 7) return Array.from({ length: t }, (_, i) => i + 1);
      if (c <= 4) return [1, 2, 3, 4, 5, '...', t];
      if (c >= t - 3) return [1, '...', t-4, t-3, t-2, t-1, t];
      return [1, '...', c-1, c, c+1, '...', t];
    });

    const modelBadge = (model) => {
      const map = {
        'openai':       'bg-sky-100 text-sky-700 border-sky-200',
        'gemini':       'bg-emerald-100 text-emerald-700 border-emerald-200',
        'groq':         'bg-violet-100 text-purple-700 border-purple-200',
        'groq-70b':     'bg-purple-100 text-purple-800 border-purple-300',
        'groq-mixtral': 'bg-fuchsia-100 text-fuchsia-700 border-fuchsia-200',
        'groq-gemma':   'bg-orange-100 text-orange-700 border-orange-200',
        'huggingface':  'bg-amber-100 text-amber-700 border-amber-200',
        'mistral':      'bg-indigo-100 text-indigo-700 border-indigo-200',
        'cerebras':     'bg-rose-100 text-rose-700 border-rose-200',
        'cohere':       'bg-teal-100 text-teal-700 border-teal-200',
        'ollama':       'bg-slate-100 text-slate-700 border-slate-300',
        'free':         'bg-gray-100 text-gray-600 border-gray-200',
      };
      const cls = map[model] || 'bg-gray-50 text-gray-500 border-gray-100';
      return `px-2 py-0.5 rounded-md text-[10px] font-bold border uppercase ${cls}`;
    };

    const fetchLogs = async () => {
      loading.value = true;
      expandedId.value = null;
      try {
        const params = { page: currentPage.value, limit: pageSize };
        if (filters.value.search)  params.search  = filters.value.search;
        if (filters.value.model)   params.model   = filters.value.model;
        if (filters.value.user_id) params.user_id = filters.value.user_id;
        const res = await axios.get(`${API_URL}/api/admin/queries`, { params });
        items.value = res.data.items;
        total.value = res.data.total;
      } catch (e) { console.error(e); }
      finally { loading.value = false; }
    };

    const applyFilters = () => { currentPage.value = 1; fetchLogs(); };
    const resetFilters = () => { filters.value = { search: '', model: '', user_id: '' }; currentPage.value = 1; fetchLogs(); };
    const goPage       = (p) => { currentPage.value = p; fetchLogs(); };
    const toggleRow    = (id) => { expandedId.value = expandedId.value === id ? null : id; };

    onMounted(async () => {
      try {
        const [usersRes, logsRes] = await Promise.all([
          axios.get(`${API_URL}/api/admin/users`),
          axios.get(`${API_URL}/api/admin/queries`, { params: { page: 1, limit: pageSize } }),
        ]);
        userOptions.value = usersRes.data.items || usersRes.data;
        items.value       = logsRes.data.items;
        total.value       = logsRes.data.total;
        // 첫 페이지 결과에서 모델 목록 추출
        const modelSet = new Set(logsRes.data.items.map(i => i.model).filter(Boolean));
        modelOptions.value = [...modelSet];
      } catch (e) { console.error(e); }
    });

    return {
      items, total, loading, expandedId, currentPage, pageSize,
      filters, modelOptions, userOptions,
      totalPages, isFiltered, pageNumbers,
      modelBadge, applyFilters, resetFilters, goPage, toggleRow,
    };
  }
};

// ─── WrongAnswersReview ───────────────────────
const WrongAnswersReview = {
  template: `
    <div class="p-8 max-w-3xl mx-auto">

      <!-- 헤더 -->
      <div class="flex items-center justify-between mb-6">
        <div>
          <h1 class="text-3xl font-bold">오답 복습</h1>
          <p v-if="!loading" class="text-sm text-gray-400 mt-1">
            <span v-if="questions.length > 0">틀린 문제 <span class="text-red-500 font-semibold">{{ questions.length }}개</span>를 다시 풀어보세요.</span>
            <span v-else>틀린 문제가 없습니다.</span>
          </p>
        </div>
        <router-link to="/" class="text-sm text-gray-500 hover:text-gray-700 flex items-center gap-1">← 대시보드</router-link>
      </div>

      <!-- 로딩 -->
      <div v-if="loading" class="flex items-center justify-center h-60 gap-3 text-gray-400">
        <span class="animate-spin inline-block w-6 h-6 border-2 border-red-400 border-t-transparent rounded-full"></span>
        <span>불러오는 중...</span>
      </div>

      <!-- 틀린 문제 없음 -->
      <div v-else-if="questions.length === 0" class="bg-white rounded-2xl shadow p-12 text-center">
        <div class="text-6xl mb-4">🎉</div>
        <p class="text-xl font-bold text-gray-700 mb-2">모든 문제를 맞혔습니다!</p>
        <p class="text-gray-400 mb-6">아직 틀린 문제가 없거나 모두 복습 완료했습니다.</p>
        <router-link to="/questions" class="inline-block bg-purple-600 text-white px-6 py-2.5 rounded-xl font-semibold hover:bg-purple-700 transition-colors">문제은행으로</router-link>
      </div>

      <template v-else>
        <!-- 점수 배너 (채점 후) -->
        <div v-if="submitted" class="mb-6 p-5 rounded-2xl text-center font-bold text-lg shadow-sm"
          :class="score.correct === score.total ? 'bg-green-100 text-green-700 border border-green-200'
                : score.correct / score.total >= 0.6 ? 'bg-blue-100 text-blue-700 border border-blue-200'
                : 'bg-orange-100 text-orange-700 border border-orange-200'">
          <div class="text-3xl font-black mb-1">{{ Math.round(score.correct / score.total * 100) }}%</div>
          <div class="text-base font-semibold">{{ score.total }}문제 중 {{ score.correct }}문제 정답</div>
          <div class="mt-3 flex justify-center gap-3">
            <button v-if="stillWrongCount > 0" @click="retryWrong"
              class="text-sm px-4 py-2 rounded-lg bg-red-500 text-white hover:bg-red-600 font-semibold transition-colors">
              틀린 {{ stillWrongCount }}문제 다시 풀기
            </button>
            <router-link to="/" class="text-sm px-4 py-2 rounded-lg bg-white border border-gray-200 text-gray-600 hover:bg-gray-50 font-medium transition-colors">
              대시보드로
            </router-link>
          </div>
        </div>

        <!-- 진행상황 바 (채점 전) -->
        <div v-if="!submitted" class="bg-white rounded-xl shadow px-5 py-4 mb-6 flex items-center gap-4">
          <span class="text-sm text-gray-500 flex-shrink-0">{{ answeredCount }} / {{ questions.length }} 답변</span>
          <div class="flex-1 bg-gray-100 rounded-full h-2">
            <div class="bg-red-400 h-2 rounded-full transition-all duration-300"
              :style="{ width: (answeredCount / questions.length * 100) + '%' }"></div>
          </div>
          <span class="text-sm font-semibold text-red-500 flex-shrink-0">{{ Math.round(answeredCount / questions.length * 100) }}%</span>
        </div>

        <!-- 문제 카드 -->
        <div class="space-y-6">
          <div v-for="(q, qi) in questions" :key="q.id"
            class="bg-white rounded-2xl shadow p-6 transition-all"
            :class="submitted ? (results[q.id]?.is_correct ? 'border-l-4 border-green-400' : 'border-l-4 border-red-400') : 'border-l-4 border-gray-200'">

            <!-- 문제 번호 + 채점 뱃지 -->
            <div class="flex items-start justify-between mb-4">
              <p class="font-semibold text-gray-800 flex-1 pr-4">
                <span class="text-red-500 font-bold mr-2">Q{{ qi + 1 }}.</span>{{ q.question }}
              </p>
              <span v-if="submitted" class="flex-shrink-0 text-xs font-bold px-2.5 py-1 rounded-full"
                :class="results[q.id]?.is_correct ? 'bg-green-100 text-green-700' : 'bg-red-100 text-red-700'">
                {{ results[q.id]?.is_correct ? '✓ 정답' : '✗ 오답' }}
              </span>
            </div>

            <!-- 선택지 -->
            <div class="space-y-2">
              <label v-for="choice in q.choices" :key="choice"
                class="flex items-center gap-3 p-3 rounded-xl border cursor-pointer transition-all"
                :class="choiceClass(q.id, choice)">
                <input type="radio"
                  :name="'rq' + q.id"
                  :value="choice"
                  v-model="userAnswers[q.id]"
                  :disabled="submitted"
                  class="accent-red-500 w-4 h-4 flex-shrink-0">
                <span class="text-sm flex-1">{{ choice }}</span>
                <span v-if="submitted && choice === results[q.id]?.correct_answer"
                  class="text-green-600 text-xs font-bold flex-shrink-0">정답</span>
                <span v-else-if="submitted && choice === userAnswers[q.id] && !results[q.id]?.is_correct"
                  class="text-red-500 text-xs flex-shrink-0">내 답</span>
              </label>
            </div>
          </div>
        </div>

        <!-- 채점 버튼 -->
        <div v-if="!submitted" class="flex justify-center mt-8">
          <button
            @click="submitAnswers"
            :disabled="submitting || answeredCount < questions.length"
            class="bg-red-500 text-white px-12 py-3.5 rounded-2xl font-bold text-lg hover:bg-red-600 disabled:opacity-40 disabled:cursor-not-allowed transition-colors flex items-center gap-3 shadow-md">
            <span v-if="submitting" class="animate-spin inline-block w-5 h-5 border-2 border-white border-t-transparent rounded-full"></span>
            {{ submitting ? '채점 중...' : answeredCount < questions.length ? '모든 문제에 답해주세요 (' + answeredCount + '/' + questions.length + ')' : '채점하기' }}
          </button>
        </div>
      </template>
    </div>
  `,
  setup() {
    const questions   = ref([]);
    const userAnswers = ref({});
    const results     = ref({});
    const submitted   = ref(false);
    const submitting  = ref(false);
    const loading     = ref(true);
    const score       = ref({ correct: 0, total: 0 });

    const answeredCount  = Vue.computed(() => Object.keys(userAnswers.value).length);
    const stillWrongCount = Vue.computed(() =>
      Object.values(results.value).filter(r => !r.is_correct).length
    );

    const fetchWrongQuestions = async () => {
      loading.value = true;
      try {
        const res = await axios.get(`${API_URL}/api/questions`);
        questions.value = res.data.filter(q => q.status === 'wrong');
      } catch (e) { console.error(e); }
      finally { loading.value = false; }
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
            question_id: q.id, user_answer: answer,
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
    };

    const retryWrong = () => {
      // 틀린 문제만 남기고 초기화
      const wrongIds = new Set(
        Object.entries(results.value).filter(([, r]) => !r.is_correct).map(([id]) => parseInt(id))
      );
      questions.value  = questions.value.filter(q => wrongIds.has(q.id));
      userAnswers.value = {};
      results.value    = {};
      submitted.value  = false;
      score.value      = { correct: 0, total: 0 };
    };

    const choiceClass = (qId, choice) => {
      if (!submitted.value) {
        return userAnswers.value[qId] === choice
          ? 'border-red-400 bg-red-50'
          : 'border-gray-200 hover:border-red-300 hover:bg-red-50/50';
      }
      const r = results.value[qId];
      if (!r) return 'border-gray-200';
      if (choice === r.correct_answer)                               return 'border-green-400 bg-green-50';
      if (choice === userAnswers.value[qId] && !r.is_correct) return 'border-red-400 bg-red-50/70';
      return 'border-gray-100 opacity-60';
    };

    onMounted(fetchWrongQuestions);
    return {
      questions, userAnswers, results, submitted, submitting, loading, score,
      answeredCount, stillWrongCount, submitAnswers, retryWrong, choiceClass,
    };
  }
};

// ─── AdminUsers ──────────────────────────────
const AdminUsers = {
  template: `
    <div class="p-8">
      <div class="flex items-center justify-between mb-6">
        <div>
          <h1 class="text-3xl font-bold">사용자 관리</h1>
          <p class="text-sm text-gray-400 mt-1">전체 {{ total }}명</p>
        </div>
        <router-link to="/admin" class="text-sm text-gray-500 hover:text-gray-700 flex items-center gap-1">← 관리자 대시보드</router-link>
      </div>

      <!-- 검색 -->
      <div class="bg-white rounded-xl shadow p-4 mb-6 flex gap-3 items-center">
        <input v-model="search" @keyup.enter="applySearch" type="text" placeholder="이름·이메일 검색"
          class="border border-gray-200 rounded-lg px-3 py-2 text-sm flex-1 focus:outline-none focus:ring-2 focus:ring-yellow-400">
        <button @click="applySearch" class="bg-yellow-500 text-gray-900 font-semibold px-4 py-2 rounded-lg text-sm hover:bg-yellow-400 transition-colors">검색</button>
        <button @click="resetSearch" class="text-gray-500 px-3 py-2 rounded-lg text-sm hover:bg-gray-100 transition-colors">초기화</button>
      </div>

      <!-- 사용자 테이블 -->
      <div class="bg-white rounded-xl shadow overflow-hidden mb-6">
        <div v-if="loading" class="flex items-center justify-center h-40 text-gray-400 gap-3">
          <span class="animate-spin inline-block w-5 h-5 border-2 border-yellow-400 border-t-transparent rounded-full"></span>
          <span>불러오는 중...</span>
        </div>
        <table v-else class="w-full text-sm">
          <thead class="bg-gray-50 border-b">
            <tr class="text-left text-gray-500">
              <th class="px-4 py-3 font-medium">ID</th>
              <th class="px-4 py-3 font-medium">이름</th>
              <th class="px-4 py-3 font-medium">이메일</th>
              <th class="px-4 py-3 font-medium">권한</th>
              <th class="px-4 py-3 font-medium">상태</th>
              <th class="px-4 py-3 font-medium">가입일</th>
              <th class="px-4 py-3 font-medium">작업</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="u in items" :key="u.id" class="border-b last:border-0 hover:bg-gray-50 transition-colors"
              :class="u.is_deleted ? 'opacity-50' : ''">
              <td class="px-4 py-3 text-gray-400">{{ u.id }}</td>
              <td class="px-4 py-3 font-medium text-gray-800">{{ u.name }}</td>
              <td class="px-4 py-3 text-gray-600">{{ u.email }}</td>
              <td class="px-4 py-3">
                <select :value="u.role" @change="changeRole(u, $event.target.value)"
                  class="border border-gray-200 rounded px-2 py-1 text-xs font-semibold focus:outline-none"
                  :class="u.role === 'admin' ? 'bg-yellow-50 text-yellow-800 border-yellow-300' : 'bg-blue-50 text-blue-700 border-blue-200'">
                  <option value="user">USER</option>
                  <option value="admin">ADMIN</option>
                </select>
              </td>
              <td class="px-4 py-3">
                <span v-if="u.is_deleted" class="text-xs font-semibold bg-red-100 text-red-600 px-2 py-0.5 rounded-full">
                  탈퇴 ({{ u.deleted_at || '-' }})
                </span>
                <span v-else class="text-xs font-semibold bg-green-100 text-green-700 px-2 py-0.5 rounded-full">활성</span>
              </td>
              <td class="px-4 py-3 text-gray-400 text-xs">{{ u.created_at || '-' }}</td>
              <td class="px-4 py-3 flex gap-2">
                <button v-if="!u.is_deleted" @click="deactivateUser(u)"
                  class="text-xs bg-red-100 text-red-600 px-3 py-1 rounded-lg hover:bg-red-200 transition-colors font-medium">
                  탈퇴
                </button>
                <button v-else @click="activateUser(u)"
                  class="text-xs bg-green-100 text-green-700 px-3 py-1 rounded-lg hover:bg-green-200 transition-colors font-medium">
                  복구
                </button>
              </td>
            </tr>
            <tr v-if="items.length === 0 && !loading">
              <td colspan="7" class="px-4 py-10 text-center text-gray-400">사용자가 없습니다.</td>
            </tr>
          </tbody>
        </table>
      </div>

      <!-- 페이지네이션 -->
      <div v-if="totalPages > 1" class="flex items-center justify-center gap-1">
        <button @click="goPage(1)" :disabled="currentPage === 1"
          class="px-2 py-1 rounded text-sm text-gray-500 hover:bg-gray-100 disabled:opacity-30">«</button>
        <button @click="goPage(currentPage - 1)" :disabled="currentPage === 1"
          class="px-3 py-1 rounded text-sm text-gray-500 hover:bg-gray-100 disabled:opacity-30">‹</button>
        <button v-for="p in pageNumbers" :key="p"
          @click="typeof p === 'number' && goPage(p)"
          :class="p === currentPage ? 'bg-yellow-500 text-gray-900 font-semibold' : p === '...' ? 'cursor-default text-gray-400' : 'text-gray-600 hover:bg-gray-100'"
          class="w-9 h-9 rounded-lg text-sm transition-colors">{{ p }}</button>
        <button @click="goPage(currentPage + 1)" :disabled="currentPage === totalPages"
          class="px-3 py-1 rounded text-sm text-gray-500 hover:bg-gray-100 disabled:opacity-30">›</button>
        <button @click="goPage(totalPages)" :disabled="currentPage === totalPages"
          class="px-2 py-1 rounded text-sm text-gray-500 hover:bg-gray-100 disabled:opacity-30">»</button>
      </div>

      <!-- 결과 메시지 -->
      <div v-if="resultMsg" class="fixed bottom-6 right-6 px-5 py-3 rounded-xl shadow-lg text-sm font-semibold transition-all"
        :class="resultOk ? 'bg-green-100 text-green-800 border border-green-200' : 'bg-red-100 text-red-700 border border-red-200'">
        {{ resultMsg }}
      </div>
    </div>
  `,
  setup() {
    const items       = ref([]);
    const total       = ref(0);
    const loading     = ref(false);
    const currentPage = ref(1);
    const pageSize    = 20;
    const search      = ref('');
    const resultMsg   = ref('');
    const resultOk    = ref(true);
    let resultTimer   = null;

    const totalPages = Vue.computed(() => Math.max(1, Math.ceil(total.value / pageSize)));
    const pageNumbers = Vue.computed(() => {
      const t = totalPages.value, c = currentPage.value;
      if (t <= 7) return Array.from({ length: t }, (_, i) => i + 1);
      if (c <= 4)       return [1, 2, 3, 4, 5, '...', t];
      if (c >= t - 3)   return [1, '...', t-4, t-3, t-2, t-1, t];
      return [1, '...', c-1, c, c+1, '...', t];
    });

    const showResult = (msg, ok = true) => {
      resultMsg.value = msg; resultOk.value = ok;
      clearTimeout(resultTimer);
      resultTimer = setTimeout(() => { resultMsg.value = ''; }, 3000);
    };

    const fetchUsers = async () => {
      loading.value = true;
      try {
        const res = await axios.get(`${API_URL}/api/admin/users`, {
          params: { search: search.value || undefined, page: currentPage.value, limit: pageSize }
        });
        items.value = res.data.items;
        total.value = res.data.total;
      } catch (e) { console.error(e); }
      finally { loading.value = false; }
    };

    const applySearch = () => { currentPage.value = 1; fetchUsers(); };
    const resetSearch = () => { search.value = ''; currentPage.value = 1; fetchUsers(); };
    const goPage      = (p) => { currentPage.value = p; fetchUsers(); };

    const changeRole = async (u, newRole) => {
      try {
        await axios.put(`${API_URL}/api/admin/users/${u.id}/role`, null, { params: { role: newRole } });
        u.role = newRole;
        showResult(`${u.name} 권한을 ${newRole}으로 변경했습니다.`);
      } catch (e) {
        showResult('권한 변경 실패: ' + (e.response?.data?.detail || e.message), false);
      }
    };

    const deactivateUser = async (u) => {
      if (!confirm(`[주의] "${u.name}" 계정을 탈퇴 처리하시겠습니까?\n탈퇴 후 해당 사용자는 로그인할 수 없습니다.`)) return;
      try {
        await axios.patch(`${API_URL}/api/admin/users/${u.id}/deactivate`);
        await fetchUsers();
        showResult(`${u.name} 계정을 탈퇴 처리했습니다.`);
      } catch (e) {
        showResult('탈퇴 처리 실패: ' + (e.response?.data?.detail || e.message), false);
      }
    };

    const activateUser = async (u) => {
      try {
        await axios.patch(`${API_URL}/api/admin/users/${u.id}/activate`);
        await fetchUsers();
        showResult(`${u.name} 계정을 복구했습니다.`);
      } catch (e) {
        showResult('복구 실패: ' + (e.response?.data?.detail || e.message), false);
      }
    };

    onMounted(fetchUsers);
    return {
      items, total, loading, currentPage, search, resultMsg, resultOk,
      totalPages, pageNumbers, applySearch, resetSearch, goPage,
      changeRole, deactivateUser, activateUser,
    };
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
          <router-link to="/review" class="p-3 rounded-lg hover:bg-gray-800 transition-colors flex items-center gap-3" active-class="bg-red-500 hover:bg-red-500">
            <span>오답 복습</span>
          </router-link>
          <template v-if="user.role === 'admin'">
            <div class="mt-4 border-t border-gray-700 pt-4 flex flex-col gap-2">
              <router-link to="/admin"
                class="p-3 rounded-lg hover:bg-gray-800 transition-colors flex items-center gap-3"
                active-class="bg-yellow-500 hover:bg-yellow-500 text-gray-900">
                <span>관리자 대시보드</span>
              </router-link>
              <router-link to="/admin/logs"
                class="p-3 rounded-lg hover:bg-gray-800 transition-colors flex items-center gap-3"
                active-class="bg-yellow-500 hover:bg-yellow-500 text-gray-900">
                <span>쿼리 로그</span>
              </router-link>
              <router-link to="/admin/users"
                class="p-3 rounded-lg hover:bg-gray-800 transition-colors flex items-center gap-3"
                active-class="bg-yellow-500 hover:bg-yellow-500 text-gray-900">
                <span>사용자 관리</span>
              </router-link>
            </div>
          </template>
          <!-- 회원 탈퇴 (숨김 메뉴) -->
          <div class="mt-auto pt-4">
            <button @click="showWithdrawModal = true"
              class="text-xs text-gray-600 hover:text-red-400 transition-colors px-1 py-1">
              회원 탈퇴
            </button>
          </div>
        </div>
      </nav>

      <!-- 탈퇴 확인 모달 1단계 -->
      <div v-if="showWithdrawModal" class="fixed inset-0 bg-black/60 flex items-center justify-center z-50">
        <div class="bg-white rounded-2xl shadow-2xl p-8 max-w-sm w-full mx-4">
          <h3 class="text-xl font-bold text-red-600 mb-3">회원 탈퇴</h3>
          <p class="text-gray-700 mb-2">정말 탈퇴하시겠습니까?</p>
          <p class="text-sm text-gray-500 mb-6">탈퇴 시 모든 학습 기록과 업로드 파일에 대한 접근이 즉시 제한됩니다.</p>
          <div class="flex gap-3 justify-end">
            <button @click="showWithdrawModal = false" class="px-4 py-2 rounded-lg bg-gray-100 text-gray-600 hover:bg-gray-200 font-medium text-sm">취소</button>
            <button @click="showWithdrawModal = false; showWithdrawModal2 = true"
              class="px-4 py-2 rounded-lg bg-red-500 text-white hover:bg-red-600 font-semibold text-sm">계속 진행</button>
          </div>
        </div>
      </div>

      <!-- 탈퇴 확인 모달 2단계 -->
      <div v-if="showWithdrawModal2" class="fixed inset-0 bg-black/70 flex items-center justify-center z-50">
        <div class="bg-white rounded-2xl shadow-2xl p-8 max-w-sm w-full mx-4 border-2 border-red-300">
          <h3 class="text-xl font-bold text-red-700 mb-3">최종 확인</h3>
          <p class="text-gray-800 font-semibold mb-2">이 작업은 되돌릴 수 없습니다.</p>
          <p class="text-sm text-gray-500 mb-6">탈퇴 후에는 관리자만 계정을 복구할 수 있습니다. 정말로 탈퇴하시겠습니까?</p>
          <div class="flex gap-3 justify-end">
            <button @click="showWithdrawModal2 = false" class="px-4 py-2 rounded-lg bg-gray-100 text-gray-600 hover:bg-gray-200 font-medium text-sm">아니오, 취소</button>
            <button @click="withdrawAccount" :disabled="withdrawing"
              class="px-4 py-2 rounded-lg bg-red-600 text-white hover:bg-red-700 font-semibold text-sm disabled:opacity-50 flex items-center gap-2">
              <span v-if="withdrawing" class="animate-spin inline-block w-3 h-3 border-2 border-white border-t-transparent rounded-full"></span>
              예, 탈퇴합니다
            </button>
          </div>
        </div>
      </div>
      <main class="flex-1 overflow-auto bg-gray-50">
        <router-view></router-view>
      </main>
    </div>
  `,
  setup() {
    const user               = ref(null);
    const loading            = ref(true);
    const showWithdrawModal  = ref(false);
    const showWithdrawModal2 = ref(false);
    const withdrawing        = ref(false);

    onMounted(async () => {
      user.value    = await checkAuth();
      loading.value = false;
    });

    const withdrawAccount = async () => {
      withdrawing.value = true;
      try {
        await axios.delete(`${API_URL}/api/me`);
        showWithdrawModal2.value = false;
        alert('탈퇴가 완료되었습니다. 이용해주셔서 감사합니다.');
        window.location.href = '/logout';
      } catch (e) {
        alert('탈퇴 처리 중 오류가 발생했습니다: ' + (e.response?.data?.detail || e.message));
      } finally {
        withdrawing.value = false;
      }
    };

    return { user, loading, showWithdrawModal, showWithdrawModal2, withdrawing, withdrawAccount };
  },
  components: { Login }
};

// ─── Router ──────────────────────────────────
const routes = [
  { path: '/',            component: Dashboard },
  { path: '/upload',      component: Upload },
  { path: '/chat',        component: Chat },
  { path: '/questions',   component: Questions },
  { path: '/review',      component: WrongAnswersReview },
  { path: '/admin',       component: AdminDashboard },
  { path: '/admin/logs',  component: QueryLogs },
  { path: '/admin/users', component: AdminUsers },
];

const router = createRouter({ history: createWebHashHistory(), routes });
createApp(App).use(router).mount('#app');
