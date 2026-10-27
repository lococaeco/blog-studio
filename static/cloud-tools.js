/* GitHub connection, exact-commit deployment status and portable updates. */
(() => {
  'use strict';
  let context;
  let dialog;
  let deploymentTimer;
  let loginTimer;
  let lastCommit = '';
  let lastPostURL = '';
  let deploymentAttempts = 0;
  let updateScheduled = false;
  const $ = id => dialog.querySelector('#cloud-' + id);
  const text = (tag, value, className) => {
    const node = document.createElement(tag);
    node.textContent = value;
    if (className) node.className = className;
    return node;
  };
  const act = callback => async event => {
    const button = event?.currentTarget;
    if (button) button.disabled = true;
    try { await callback(event); }
    catch (error) { $('message').textContent = error.message || '작업을 완료하지 못했어요.'; context.notify?.(error.message, true); }
    finally { if (button && button.isConnected) button.disabled = updateScheduled && button.id === 'cloud-apply-update'; }
  };
  function safeLink(url, label) {
    const link = text('a', label);
    try {
      if (!['https:', 'http:'].includes(new URL(url).protocol)) return text('span', label);
      link.href = url;
    } catch (_) { return text('span', label); }
    link.target = '_blank'; link.rel = 'noopener noreferrer';
    return link;
  }
  async function refreshConnection() {
    const status = await context.api('/api/cloud/status');
    $('account').textContent = status.connected ? `${status.login} 계정으로 연결됨` : 'GitHub에 연결하지 않았어요.';
    $('dependencies').replaceChildren();
    if (!status.git_available) $('dependencies').append(safeLink('https://git-scm.com/downloads', 'Git 설치하기'), text('span', ' · GitHub 전송과 저장소 가져오기에 필요합니다.'));
    $('browser-login').disabled = !status.gh_available;
    $('browser-note').replaceChildren(text('span', status.gh_available ? 'GitHub CLI의 로그인 저장소를 사용합니다.' : '브라우저 로그인에는 GitHub CLI가 필요합니다. 토큰 연결은 바로 사용할 수 있어요. '));
    if (!status.gh_available) $('browser-note').append(safeLink('https://cli.github.com/', 'GitHub CLI 설치'));
    $('repo-current').textContent = status.repository ? `연결된 저장소: ${status.repository}` : '블로그 origin이 GitHub 저장소에 연결되어 있지 않습니다.';
    return status;
  }
  async function loginPoll() {
    clearTimeout(loginTimer);
    const status = await context.api('/api/cloud/login-status');
    $('login-code').textContent = status.code || '';
    $('message').textContent = status.message || '';
    if (status.running) loginTimer = setTimeout(() => loginPoll().catch(() => {}), 1500);
    else await refreshConnection();
  }
  async function checkUpdate() {
    $('update-status').textContent = '새 버전을 확인하고 있어요…';
    const result = await context.api('/api/update/check');
    $('update-status').textContent = `현재 ${result.current} · 최신 ${result.latest} — ${result.message}`;
    $('release-link').replaceChildren(safeLink(result.release_url, '릴리스와 변경 내역 보기'));
    $('download-update').disabled = !result.available || !result.can_apply;
    if (!result.can_apply) $('update-status').append(' 이 실행 위치에서는 자동 교체할 수 없습니다. 릴리스의 실행 파일을 사용하거나 앱을 쓰기 가능한 폴더로 옮겨 주세요.');
  }
  async function pollDeployment() {
    clearTimeout(deploymentTimer);
    if (!lastCommit) lastCommit = context.getDraft?.()?.last_commit || '';
    if (!lastCommit) { $('deployment-status').textContent = '먼저 글을 GitHub에 전송해 주세요.'; return; }
    const requestedCommit = lastCommit;
    try {
      const result = await context.api('/api/cloud/deployment', 'POST', { commit: requestedCommit });
      if (requestedCommit !== lastCommit) return;
      $('deployment-status').textContent = result.message + (result.permission_note ? ' ' + result.permission_note : '');
      $('deployment-status').dataset.state = result.state;
      $('deployment-runs').replaceChildren();
      for (const run of result.runs || []) {
        const row = text('div', '', 'cloud-run');
        const state = run.conclusion || run.status || '대기 중';
        row.append(safeLink(run.url, run.name), text('span', state));
        if (['failure', 'timed_out', 'cancelled', 'action_required'].includes(run.conclusion)) {
          const retry = text('button', '실패한 작업 다시 실행', 'button subtle');
          retry.onclick = act(async () => {
            if (!window.confirm('이 커밋의 실패한 GitHub 작업을 다시 실행할까요?')) return;
            await context.api('/api/cloud/retry', 'POST', { run_id: run.id });
            await pollDeployment();
          });
          row.append(retry);
        }
        $('deployment-runs').append(row);
      }
      $('published-link').replaceChildren();
      if (result.state === 'published') {
        $('published-link').append(safeLink(lastPostURL || result.url, '게시된 글 열기 ↗'));
        context.notify?.('GitHub Pages 게시가 완료됐어요.');
      } else if (result.state !== 'failed') {
        deploymentAttempts += 1;
        if (deploymentAttempts < 40) deploymentTimer = setTimeout(pollDeployment, 30000);
        else $('deployment-status').append(' 게시 확인이 오래 걸리고 있어요. 작업 로그를 확인하거나 다시 확인을 눌러 주세요.');
      }
    } catch (error) {
      $('deployment-status').textContent = error.message + ' 아래 새로 확인 버튼으로 다시 확인할 수 있어요.';
    }
  }
  function install(options) {
    context = options;
    const openButton = text('button', 'GitHub · 업데이트', 'button subtle cloud-open');
    openButton.id = 'cloud-open';
    options.host.append(openButton);
    dialog = document.createElement('dialog');
    dialog.id = 'cloud-dialog'; dialog.className = 'dialog cloud-dialog';
    dialog.setAttribute('aria-labelledby', 'cloud-title');
    dialog.innerHTML = `<div class="dialog-heading"><div><div class="eyebrow">CONNECTED WRITING</div><h2 id="cloud-title">GitHub와 앱 관리</h2></div><button type="button" id="cloud-close" class="icon-button" aria-label="GitHub 관리 닫기">×</button></div>
      <p id="cloud-message" role="status" class="dialog-note"></p>
      <details open><summary>GitHub 연결</summary><p id="cloud-account"></p><div id="cloud-dependencies" class="dialog-note"></div>
        <button id="cloud-browser-login" class="button primary">브라우저로 로그인</button>
        <p id="cloud-browser-note" class="dialog-note"></p><strong id="cloud-login-code" class="cloud-code"></strong>
        <a href="https://github.com/login/device" target="_blank" rel="noopener noreferrer">인증 코드 입력 페이지 열기</a>
        <details><summary>개인 액세스 토큰으로 연결</summary><p class="dialog-note">토큰은 이 앱 실행 동안만 메모리에 보관됩니다. 저장소 작업에는 Contents 읽기·쓰기, 게시 확인에는 Actions·Pages·Deployments 읽기 권한을 선택하세요. 재실행에는 Actions 쓰기 권한이 필요합니다.</p>
          <label for="cloud-token">GitHub 토큰</label><input type="password" id="cloud-token" autocomplete="off" placeholder="GitHub 개인 액세스 토큰">
          <button id="cloud-connect" class="button primary">이 앱에 연결</button>
          <a href="https://github.com/settings/personal-access-tokens" target="_blank" rel="noopener noreferrer">GitHub에서 토큰 만들기</a>
        </details><button id="cloud-disconnect" class="button subtle">이 앱 연결 해제</button>
        <p id="cloud-repo-current" class="dialog-note"></p>
        <details><summary>Git 작성자 설정</summary><label for="cloud-author-name">이름</label><input id="cloud-author-name" maxlength="100"><label for="cloud-author-email">이메일</label><input id="cloud-author-email" type="email"><button id="cloud-author-save" class="button subtle">이 블로그에 저장</button></details>
        <details><summary>다른 저장소 가져오기</summary><p class="dialog-note">새 폴더에 복제합니다. 가져온 폴더는 시작 창에서 선택할 수 있어요.</p><button id="cloud-list-repos" class="button subtle">내 저장소 목록</button><select id="cloud-repos"><option value="">저장소 선택</option></select><label for="cloud-clone-repo">GitHub 저장소</label><input id="cloud-clone-repo" placeholder="소유자/저장소"><label for="cloud-clone-path">저장할 새 폴더의 전체 경로</label><input id="cloud-clone-path" placeholder="예: /home/me/my-blog 또는 C:\\Users\\me\\my-blog"><button id="cloud-clone" class="button primary">새 폴더로 가져오기</button><p id="cloud-clone-result" class="dialog-note"></p></details>
      </details>
      <details open id="cloud-deployment"><summary>사이트 게시 상태</summary><p id="cloud-deployment-status">GitHub 전송 후 이곳에서 실제 게시 완료를 확인합니다.</p><div id="cloud-deployment-runs"></div><div id="cloud-published-link"></div><button id="cloud-refresh-deployment" class="button subtle">다시 확인</button></details>
      <details open><summary>앱 업데이트</summary><p id="cloud-update-status">업데이트 확인을 눌러 새 버전을 찾아보세요.</p><div id="cloud-release-link"></div><div class="cloud-actions"><button id="cloud-check-update" class="button subtle">업데이트 확인</button><button id="cloud-download-update" class="button subtle" disabled>새 버전 다운로드</button><button id="cloud-apply-update" class="button primary" disabled>저장 후 업데이트 적용</button></div><p class="dialog-note">공식 릴리스와 SHA-256을 검증하고, 이전 실행 파일을 백업한 뒤 앱을 재시작합니다.</p></details>`;
    document.body.append(dialog);
    openButton.onclick = act(async () => { dialog.showModal(); await refreshConnection(); });
    $('close').onclick = () => dialog.close();
    $('browser-login').onclick = act(async () => { await context.api('/api/cloud/login', 'POST', {}); await loginPoll(); });
    $('connect').onclick = act(async () => {
      const token = $('token').value; $('token').value = '';
      const result = await context.api('/api/cloud/connect', 'POST', { token });
      $('message').textContent = result.message; await refreshConnection();
    });
    $('disconnect').onclick = act(async () => { const result = await context.api('/api/cloud/disconnect', 'POST', {}); $('message').textContent = result.message; await refreshConnection(); });
    $('author-save').onclick = act(async () => { const result = await context.api('/api/cloud/author', 'POST', { name: $('author-name').value, email: $('author-email').value }); $('message').textContent = result.message; });
    $('list-repos').onclick = act(async () => {
      const result = await context.api('/api/cloud/repositories'); $('repos').replaceChildren(new Option('저장소 선택', ''));
      for (const item of result.repositories) $('repos').append(new Option(item.name + (item.private ? ' · 비공개' : ''), item.name));
    });
    $('repos').onchange = () => { $('clone-repo').value = $('repos').value; };
    $('clone').onclick = act(async () => { $('clone-result').textContent = '저장소를 가져오고 있어요…'; const result = await context.api('/api/cloud/clone', 'POST', { repository: $('clone-repo').value, destination: $('clone-path').value }); $('clone-result').textContent = result.message + ' ' + result.path; });
    $('refresh-deployment').onclick = act(async () => { deploymentAttempts = 0; await pollDeployment(); });
    $('check-update').onclick = act(checkUpdate);
    $('download-update').onclick = act(async () => { $('update-status').textContent = '업데이트를 다운로드하고 검증하고 있어요…'; const result = await context.api('/api/update/download', 'POST', {}); $('update-status').textContent = result.message; $('apply-update').disabled = !result.ready || !result.can_apply; });
    $('apply-update').onclick = act(async () => {
      if (!window.confirm('현재 글을 저장하고 앱을 종료한 뒤 새 버전으로 다시 실행합니다. 진행할까요?')) return;
      await context.saveDraft?.();
      const result = await context.api('/api/update/apply', 'POST', {});
      $('update-status').textContent = result.message;
      updateScheduled = Boolean(result.scheduled);
      $('apply-update').disabled = true;
    });
    return { open: () => openButton.click() };
  }
  function track(commit, url) {
    if (!dialog || !/^[a-f0-9]{40}$/i.test(commit || '')) return;
    lastCommit = commit; lastPostURL = url || '';
    deploymentAttempts = 0;
    $('deployment').open = true;
    pollDeployment();
  }
  window.StudioCloud = { install, track };
})();
