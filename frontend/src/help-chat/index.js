import styles from './help-chat.css?inline';

const markup = `<div class="chat" id="help-chat" hidden>
    <aside class="chat-panel" id="chat-panel" aria-labelledby="chat-title" hidden>
      <header class="chat-header">
        <span class="avatar" aria-hidden="true"><span class="bubble-icon"></span></span>
        <div class="chat-title"><h2 id="chat-title">사용 안내</h2><p>Creative Checker</p></div>
        <button type="button" class="chat-action" id="new-chat">새 대화</button>
        <button type="button" class="chat-action" id="close-chat" aria-label="사용 안내 닫기"><span class="close-icon" aria-hidden="true"></span></button>
      </header>
      <div class="messages" id="messages" tabindex="0" role="region" aria-label="대화 내용">
        <div class="welcome"><strong>무엇을 도와드릴까요?</strong><p>Creative Checker가 하는 일과 포스터 검수를 시작하는 방법을 알려드려요.</p></div>
        <div class="message-list" id="message-list" role="log" aria-label="대화 메시지"></div>
      </div>
      <div class="quick-actions" aria-label="추천 질문">
        <button type="button" data-guide="about">프로젝트 소개 <span aria-hidden="true">↗</span></button>
        <button type="button" data-guide="start">포스터 검수 시작 방법 <span aria-hidden="true">↗</span></button>
      </div>
      <form id="chat-form" class="composer">
        <p id="chat-error" class="error" role="alert" hidden></p>
        <div class="input-row">
          <label class="sr-only" for="question">질문 입력</label>
          <textarea id="question" rows="1" maxlength="500" placeholder="예: PDF 없이도 검수할 수 있나요?"></textarea>
          <button type="submit" class="send" id="send" aria-label="질문 보내기" disabled><span class="arrow-up" aria-hidden="true"></span></button>
        </div>
        <p class="composer-note">질문은 OpenAI로 전송됩니다. 민감한 내용은 입력하지 마세요.</p>
      </form>
    </aside>
    <button type="button" class="launcher" id="launcher" aria-controls="chat-panel" aria-expanded="false">
      <span class="bubble-icon" id="launcher-bubble" aria-hidden="true"></span>
      <span class="close-icon" id="launcher-close" aria-hidden="true" hidden></span>
      <span id="launcher-label">사용 안내</span>
    </button>
  </div>`;

const guide = {
  about: {
    title: '프로젝트 소개',
    answer: 'Creative Checker는 기준 문서에 적힌 요구사항과 창작물 포스터를 비교하는 검수 서비스예요.\n\n예를 들어 행사명, 날짜, 필수 문구가 포스터에 제대로 들어갔는지 확인할 수 있어요. 자동 검수는 확인할 항목과 근거를 정리하고, 최종 판단은 담당자가 해요.',
  },
  start: {
    title: '포스터 검수 시작 방법',
    answer: '1. 자료 입력에서 기준 PDF를 올리거나 요구사항을 직접 입력해요.\n2. 기준 확인에서 문서 원문과 비교해 빠진 내용이나 잘못 읽은 부분을 확인해요.\n3. 포스터 이미지를 올리고 기준을 승인한 뒤 검수를 시작해요.\n4. 결과 확인에서 충족·위반·확인 필요 항목과 근거를 살펴봐요.',
  },
};

const apiPrefix = '/api/framework/v1';

class ChatRequestError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

const errorMessages = {
  HELP_CHAT_INVALID_INPUT: '질문을 확인해 주세요.',
  HELP_CHAT_NOT_CONFIGURED: '이 서버에서는 자유 질문을 아직 사용할 수 없어요.',
  HELP_CHAT_BUSY: '답변을 준비 중이에요. 잠시 후 다시 보내 주세요.',
  HELP_CHAT_RATE_LIMITED: '질문을 너무 많이 보냈어요. 잠시 후 다시 시도해 주세요.',
  HELP_CHAT_PROVIDER_LIMIT: '자유 질문 서비스의 사용량 한도에 도달했어요. 잠시 후 다시 시도해 주세요.',
  HELP_CHAT_TIMEOUT: '답변이 늦어지고 있어요. 잠시 후 다시 시도해 주세요.',
  CSRF_FAILED: '보안 확인에 실패했어요. 다시 시도해 주세요.',
};

async function readJson(response) {
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    const code = body?.error?.code;
    const message = response.status === 401 ? '다시 로그인해 주세요.'
      : Object.hasOwn(errorMessages, code) ? errorMessages[code] : '답변을 가져오지 못했어요. 다시 시도해 주세요.';
    throw new ChatRequestError(message, response.status);
  }
  return body;
}

async function requestReply(question, history, signal) {
  const options = { credentials: 'same-origin', cache: 'no-store', signal };
  const csrf = await readJson(await fetch(`${apiPrefix}/csrf`, options));
  if (typeof csrf?.csrf_token !== 'string' || !csrf.csrf_token) {
    throw new ChatRequestError('보안 확인을 준비하지 못했어요. 다시 시도해 주세요.');
  }
  const response = await readJson(await fetch(`${apiPrefix}/help/chat`, {
    ...options, method: 'POST', body: JSON.stringify({ question, history }),
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf.csrf_token },
  }));
  if (typeof response?.reply !== 'string' || !response.reply.trim()) {
    throw new ChatRequestError('답변을 확인하지 못했어요. 다시 시도해 주세요.');
  }
  return response.reply;
}

/** Mount outside the route/reset subtree. Pass only a server-confirmed account ID. */
export function mountHelpChat({
  container = document.body, userId = null, position = 'bottom-right', onSessionExpired,
} = {}) {
  const host = document.createElement('div');
  const shadow = host.attachShadow({ mode: 'open' });
  const template = document.createElement('template');
  template.innerHTML = markup;
  const style = document.createElement('style');
  style.textContent = styles;
  shadow.append(style, template.content.cloneNode(true));

  const find = id => shadow.getElementById(id);
  const chat = find('help-chat');
  chat.dataset.position = position;
  const panel = find('chat-panel');
  const launcher = find('launcher');
  const closeButton = find('close-chat');
  const question = find('question');
  const send = find('send');
  const form = find('chat-form');
  const messages = find('messages');
  const messageList = find('message-list');
  const error = find('chat-error');
  const quickButtons = [...shadow.querySelectorAll('[data-guide]')];
  let activeUser = null;
  let activeRequest = null;
  let history = [];

  function scrollToLatest() {
    messages.scrollTop = messages.scrollHeight;
  }

  function setPanel(open, focus = true) {
    panel.hidden = !open;
    launcher.setAttribute('aria-expanded', String(open));
    find('launcher-bubble').hidden = open;
    find('launcher-close').hidden = !open;
    find('launcher-label').textContent = open && position !== 'header' ? '안내 닫기' : '사용 안내';
    if (open) scrollToLatest();
    if (focus) (open ? closeButton : launcher).focus({ preventScroll: true });
  }

  function addMessage(speaker, text, pending = false) {
    const bubble = document.createElement('div');
    bubble.className = `message ${speaker}${pending ? ' pending' : ''}`;
    const label = document.createElement('span');
    label.className = 'sr-only';
    label.textContent = speaker === 'user' ? '나: ' : '사용 안내: ';
    bubble.append(label, document.createTextNode(text));
    messageList.append(bubble);
    scrollToLatest();
    return bubble;
  }

  function syncButtons() {
    const busy = activeRequest !== null;
    send.disabled = busy || !question.value.trim();
    for (const button of quickButtons) button.disabled = busy;
  }

  function showError(message = '') {
    error.textContent = message;
    error.hidden = !message;
  }

  function reset() {
    activeRequest?.abort();
    activeRequest = null;
    history = [];
    messageList.replaceChildren();
    question.value = '';
    showError();
    messages.scrollTop = 0;
    syncButtons();
  }

  function setUser(id) {
    const next = typeof id === 'string' && id.trim() ? id : null;
    if (next === activeUser) return;
    activeUser = next;
    reset();
    setPanel(false, false);
    chat.hidden = !activeUser;
  }

  function remember(questionText, reply) {
    history.push({ question: questionText, reply });
    if (history.length > 3) history.shift();
  }

  launcher.addEventListener('click', () => setPanel(panel.hidden));
  closeButton.addEventListener('click', () => setPanel(false));
  panel.addEventListener('keydown', event => {
    // The review screen also listens for number shortcuts and Escape.
    event.stopPropagation();
    if (event.key === 'Escape' && !event.isComposing && event.keyCode !== 229) setPanel(false);
  });
  panel.addEventListener('paste', event => event.stopPropagation());
  find('new-chat').addEventListener('click', () => {
    reset();
    question.focus();
  });

  for (const button of quickButtons) {
    button.addEventListener('click', () => {
      const { title, answer } = guide[button.dataset.guide];
      showError();
      addMessage('user', title);
      addMessage('assistant', answer);
      remember(title, answer);
    });
  }

  question.addEventListener('input', syncButtons);
  question.addEventListener('keydown', event => {
    if (event.key !== 'Enter' || event.shiftKey || event.isComposing || event.keyCode === 229) return;
    event.preventDefault();
    if (!send.disabled) form.requestSubmit();
  });
  form.addEventListener('submit', async event => {
    event.preventDefault();
    const text = question.value.trim();
    if (!activeUser || !text || activeRequest) return;
    const controller = new AbortController();
    activeRequest = controller;
    showError();
    const sent = addMessage('user', text);
    const pending = addMessage('assistant', '답변을 준비하고 있어요…', true);
    question.value = '';
    syncButtons();
    question.focus();
    try {
      const reply = await requestReply(text, history, controller.signal);
      if (activeRequest !== controller) return;
      pending.remove();
      addMessage('assistant', reply);
      remember(text, reply);
    } catch (failure) {
      if (activeRequest !== controller) return;
      if (failure.status === 401) {
        setUser(null);
        onSessionExpired?.();
        return;
      }
      pending.remove();
      sent.remove();
      if (!question.value) question.value = text;
      showError(failure instanceof ChatRequestError ? failure.message : '서버에 연결하지 못했어요. 다시 시도해 주세요.');
    } finally {
      if (activeRequest === controller) {
        activeRequest = null;
        syncButtons();
      }
    }
  });

  setUser(userId);
  container.append(host);
  return {
    setUser,
    setContainer(nextContainer) {
      if (host.parentNode === nextContainer) return;
      setPanel(false, false);
      nextContainer.append(host);
    },
    destroy() {
      setUser(null);
      host.remove();
    },
  };
}
