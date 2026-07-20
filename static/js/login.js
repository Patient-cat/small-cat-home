function handleLogin(e) {
  e.preventDefault();
  var btn = document.getElementById('btnSubmit');
  var err = document.getElementById('errorMsg');
  var user = document.getElementById('username').value.trim();
  var pass = document.getElementById('password').value.trim();

  if (!user || !pass) {
    err.textContent = '请输入用户名和密码';
    return;
  }

  btn.textContent = '验证中...';
  btn.style.opacity = '0.7';
  err.textContent = '';

  fetch('/api/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username: user, password: pass })
  })
  .then(function(r) { return r.json(); })
  .then(function(d) {
    if (d.ok) {
      btn.textContent = '✓ 登录成功';
      btn.style.background = 'linear-gradient(135deg, #22c55e, #00d4aa)';
      setTimeout(function() { window.location.href = d.redirect || '/hall'; }, 400);
    } else {
      btn.textContent = '登 录';
      btn.style.opacity = '1';
      err.textContent = d.error || '登录失败，请重试';
    }
  })
  .catch(function() {
    btn.textContent = '登 录';
    btn.style.opacity = '1';
    err.textContent = '网络错误，请检查服务是否启动';
  });
}

// Auto-focus
document.getElementById('username').focus();

