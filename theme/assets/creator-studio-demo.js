(() => {
  if (customElements.get('creator-studio-demo')) return;
  let turnstileScript;
  const loadTurnstile = () => {
    if (window.turnstile) return Promise.resolve(window.turnstile);
    if (!turnstileScript) {
      turnstileScript = new Promise((resolve, reject) => {
        const script = document.createElement('script');
        script.src = 'https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit';
        script.async = true;
        script.onload = () => window.turnstile ? resolve(window.turnstile) : reject();
        script.onerror = reject;
        document.head.append(script);
      }).catch(() => {
        turnstileScript = undefined;
        throw new Error('Verification could not load. Please try again.');
      });
    }
    return turnstileScript;
  };
  customElements.define('creator-studio-demo', class extends HTMLElement {
    connectedCallback() {
      if (this.shadowRoot) return;
      const template = this.querySelector('template');
      if (!template) return;
      const root = this.attachShadow({ mode: 'open' });
      root.append(template.content.cloneNode(true));
      const get = id => root.getElementById(id);
      const form = get('studio');
      const idea = get('idea');
      const wording = get('wording');
      const reference = get('ref');
      const finished = get('finished');
      const revisionPrompt = get('revision-prompt');
      const revisionPanel = get('revision-panel');
      const revise = get('revise');
      const modes = [...root.querySelectorAll('[name="studio-mode"]')];
      const size = get('size');
      const approve = get('approve');
      const art = get('art');
      const status = get('status');
      const approval = get('approval');
      const colors = [...root.querySelectorAll('[data-color]')];
      const endpoint = this.dataset.apiEndpoint;
      const siteKey = this.dataset.turnstileSiteKey;
      let widgetId;
      let busy = false;
      let color = 'Black';
      let objectUrl;
      let generatedUrl;
      let revisionsUsed = 0;
      let hasPreview = false;

      const isUploadMode = () => modes.find(input => input.checked).value === 'upload';
      const validFile = file => ['image/png', 'image/jpeg', 'image/webp'].includes(file.type) && file.size <= 25_000_000;
      const fileAsDataUrl = file => new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result);
        reader.onerror = () => reject(new Error('The image could not be read.'));
        reader.readAsDataURL(file);
      });
      const setBusy = value => {
        busy = value;
        get('preview').disabled = busy || (Boolean(siteKey) && !isUploadMode() && Boolean(generatedUrl));
        revise.disabled = busy || revisionsUsed >= 2;
        [idea, wording, reference, finished, ...modes].forEach(input => { input.disabled = busy; });
        form.setAttribute('aria-busy', String(busy));
      };
      const showImage = (src, alt) => {
        const image = new Image();
        image.alt = alt;
        image.src = src;
        art.replaceChildren(image);
        hasPreview = true;
        approve.disabled = false;
      };
      const resetPreview = () => {
        if (objectUrl) URL.revokeObjectURL(objectUrl);
        objectUrl = undefined;
        generatedUrl = undefined;
        revisionsUsed = 0;
        hasPreview = false;
        approve.disabled = true;
        revisionPanel.hidden = true;
        revise.disabled = false;
        get('revision-count').textContent = '2 revisions available';
        approval.textContent = '';
        const placeholder = document.createElement('span');
        placeholder.className = 'placeholder';
        placeholder.textContent = 'FRONT PRINT AREA';
        art.replaceChildren(placeholder);
      };
      const updateVariant = () => {
        get('variant').textContent = '· ' + color + ' · ' + size.value;
        get('tee').setAttribute('fill', color === 'Black' ? '#30302f' : '#f7f6f2');
        art.style.color = color === 'Black' ? '#d5af52' : '#725411';
        colors.forEach(button => button.setAttribute('aria-pressed', String(button.dataset.color === color)));
        approval.textContent = '';
        approve.disabled = !hasPreview;
      };
      const requestDesign = async payload => {
        if (!endpoint) throw new Error('Creator Studio is not connected yet.');
        const headers = { 'Content-Type': 'application/json' };
        if (siteKey) {
          const turnstile = await loadTurnstile();
          if (widgetId === undefined) {
            widgetId = turnstile.render(get('guest-check'), {
              sitekey: siteKey,
              action: 'creator_studio',
              size: 'flexible',
              'response-field': false
            });
          }
          const responseToken = turnstile.getResponse(widgetId);
          if (!responseToken) throw new Error('Complete the verification, then try again.');
          payload.turnstile_token = responseToken;
        } else {
          const token = get('preview-token').value.trim();
          if (!token) throw new Error('Enter your Creator Studio preview token to use AI.');
          headers.Authorization = 'Bearer ' + token;
        }
        try {
          const response = await fetch(endpoint, {
            method: 'POST',
            headers,
            body: JSON.stringify(payload)
          });
          const result = await response.json().catch(() => ({}));
          if (!response.ok) throw new Error(result.error || 'The design could not be generated.');
          return result;
        } finally {
          if (widgetId !== undefined) window.turnstile.reset(widgetId);
        }
      };

      colors.forEach(button => button.addEventListener('click', () => {
        color = button.dataset.color;
        updateVariant();
      }));
      size.addEventListener('change', updateVariant);
      [idea, wording, reference, finished].forEach(input => input.addEventListener('input', () => {
        if (siteKey && generatedUrl && !isUploadMode()) return;
        resetPreview();
        setBusy(false);
        status.textContent = '';
      }));
      modes.forEach(input => input.addEventListener('change', () => {
        get('create-fields').hidden = isUploadMode();
        get('upload-fields').hidden = !isUploadMode();
        status.textContent = '';
        resetPreview();
        setBusy(false);
      }));
      form.addEventListener('submit', async event => {
        event.preventDefault();
        if (busy || (siteKey && generatedUrl && !isUploadMode())) return;
        resetPreview();
        const upload = isUploadMode();
        const file = (upload ? finished : reference).files[0];
        if (upload && !file) {
          status.textContent = 'Choose your finished design to see it on the shirt.';
          finished.focus();
          return;
        }
        if (!upload && !file && !idea.value.trim() && !wording.value.trim()) {
          status.textContent = 'Add an idea, reference, or exact wording to start.';
          idea.focus();
          return;
        }
        if (file && !validFile(file)) {
          status.textContent = 'Choose a PNG, JPG or WebP up to 25 MB.';
          return;
        }
        if (upload) {
          objectUrl = URL.createObjectURL(file);
          showImage(objectUrl, 'Your finished design on the shirt');
          status.textContent = 'Your finished design is shown as uploaded. AI was not used.';
          return;
        }
        try {
          setBusy(true);
          status.textContent = 'Generating your design…';
          const result = await requestDesign({
            prompt: idea.value.trim() || 'Create a bold faith-inspired apparel graphic.',
            wording: wording.value.trim(),
            reference_data_url: file ? await fileAsDataUrl(file) : null,
            revision: 0
          });
          generatedUrl = result.image_url;
          showImage(generatedUrl, 'AI-generated design on the shirt');
          revisionPanel.hidden = false;
          status.textContent = 'Your first design is ready. You can request up to two revisions.';
        } catch (error) {
          status.textContent = error.message;
        } finally {
          setBusy(false);
        }
      });
      revise.addEventListener('click', async () => {
        const prompt = revisionPrompt.value.trim();
        if (!generatedUrl || !prompt || revisionsUsed >= 2) {
          status.textContent = revisionsUsed >= 2 ? 'You have used both revisions.' : 'Describe the change you want.';
          if (!prompt) revisionPrompt.focus();
          return;
        }
        try {
          setBusy(true);
          status.textContent = 'Creating revision ' + (revisionsUsed + 1) + ' of 2…';
          const result = await requestDesign({
            prompt,
            wording: wording.value.trim(),
            reference_url: generatedUrl,
            revision: revisionsUsed + 1
          });
          revisionsUsed += 1;
          generatedUrl = result.image_url;
          showImage(generatedUrl, 'Revised AI-generated design on the shirt');
          revisionPrompt.value = '';
          const remaining = 2 - revisionsUsed;
          get('revision-count').textContent = remaining + (remaining === 1 ? ' revision available' : ' revisions available');
          status.textContent = 'Revision ' + revisionsUsed + ' is ready.';
        } catch (error) {
          status.textContent = error.message;
        } finally {
          setBusy(false);
        }
      });
      approve.addEventListener('click', () => {
        approval.textContent = 'Design approved for your ' + color.toLowerCase() + ' tee, size ' + size.value + '.';
        approve.disabled = true;
      });
      this.cleanupPreview = resetPreview;
    }
    disconnectedCallback() {
      this.cleanupPreview?.();
    }
  });
})();
