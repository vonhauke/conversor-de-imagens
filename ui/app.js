/* Conversor de Imagens - logica da interface.
   O Python (main.py) expoe a API em window.pywebview.api e manda eventos
   chamando Conversor.receber({tipo, ...}). */

const $ = (id) => document.getElementById(id);

window.__erros = [];
window.addEventListener("error", (e) => window.__erros.push(String(e.message)));
window.addEventListener("unhandledrejection", (e) => window.__erros.push(String(e.reason)));

const Conversor = (() => {
  const s = {
    itens: [],
    convertendo: false,
    cancelando: false,
    lotes: 0,          // lotes terminados (o teste automatico usa)
    formato: "jpg",
    comPerda: [],
    resumo: { texto: "Nenhum arquivo na fila", botao: "Converter tudo", habilitado: false },
    destino: { texto: "", caminho: "", escolhida: false },
  };
  let api = null;

  const ROTULO = {
    pendente: ["Na fila", ""],
    convertendo: ["Convertendo…", "tag-acento"],
    concluido: ["Concluído", "tag-sucesso"],
    erro: ["Erro", "tag-perigo"],
    cancelado: ["Cancelado", ""],
  };
  const plural = (n, um, varios) => `${n} ${n === 1 ? um : varios}`;

  /* ---------- fila ---------- */
  function desenharFila() {
    const n = s.itens.length;
    $("soltar").classList.toggle("oculto", n > 0);
    $("fila").classList.toggle("oculto", n === 0);
    $("fila-conta").textContent = n ? plural(n, "arquivo", "arquivos") : "";
    $("subtitulo").textContent = n ? `${plural(n, "arquivo", "arquivos")} na fila` : "Converta várias imagens de uma vez";
    // pastas com centenas de fotos: o HTML e montado de uma vez so
    $("lista").innerHTML = s.itens.map((it, i) => `
      <div class="item" data-i="${i}">
        <div class="item-icone">${DS.escapar(it.ext)}</div>
        <div class="item-meio">
          <div class="item-titulo" title="${DS.escapar(it.path)}">${DS.escapar(it.nome)}</div>
          <div class="item-sub">${DS.escapar(it.tamanho)}</div>
        </div>
        <div class="item-estado">${estadoHTML(it)}</div>
        <button class="btn btn-icone remover" title="Remover da fila"${s.convertendo ? " disabled" : ""}>${icone("x", 15)}</button>
      </div>`).join("");
  }

  function estadoHTML(it) {
    const [rotulo, classe] = ROTULO[it.status] || ROTULO.pendente;
    let html = `<span class="tag ${classe}">${it.status === "concluido" ? icone("check", 12, 2.5) : ""}${rotulo}</span>`;
    if (it.status === "erro") html += `<button class="btn-link ver-log">ver detalhes</button>`;
    return html;
  }

  function pintarItem(i) {
    const linha = document.querySelector(`.item[data-i="${i}"]`);
    if (!linha) return;
    const it = s.itens[i];
    linha.classList.toggle("ativo", it.status === "convertendo");
    linha.querySelector(".item-estado").innerHTML = estadoHTML(it);
  }

  function atualizarControles() {
    $("barra-parada").classList.toggle("oculto", s.convertendo);
    $("barra-andando").classList.toggle("oculto", !s.convertendo);
    $("resumo").textContent = s.resumo.texto;
    $("btn-converter-texto").textContent = s.resumo.botao;
    $("btn-converter").disabled = !s.resumo.habilitado || s.convertendo;
    $("btn-adicionar").disabled = s.convertendo;
    document.querySelectorAll(".remover").forEach((b) => (b.disabled = s.convertendo));
    $("btn-cancelar").disabled = s.cancelando;
    $("btn-cancelar").textContent = s.cancelando ? "Cancelando…" : "Cancelar";
    $("pasta").textContent = s.destino.texto;
    $("pasta").title = s.destino.caminho || "";
    $("btn-padrao").classList.toggle("oculto", !s.destino.escolhida);
    $("btn-abrir-pasta").classList.toggle("oculto", !s.destino.caminho);
  }

  /* ---------- opcoes ---------- */
  function mostrarFormato(f) {
    s.formato = f;
    document.querySelectorAll("#formatos button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.f === f)));
    $("painel-qualidade").classList.toggle("oculto", !s.comPerda.includes(f));
    $("painel-raw-saida").classList.toggle("oculto", f !== "raw");
  }
  function mudarOpcao(chave, valor) { api.definir_opcoes({ [chave]: valor }); }

  /* ---------- avisos e log ---------- */
  function aviso(titulo, texto) {
    $("aviso-titulo").textContent = titulo;
    $("aviso-texto").textContent = texto || "";
    $("aviso").classList.remove("oculto", "entra"); void $("aviso").offsetWidth; $("aviso").classList.add("entra");
  }
  const fecharAviso = () => $("aviso").classList.add("oculto");

  async function verLog(path) {
    const texto = await api.ver_log(path);
    const it = s.itens.find((x) => x.path === path);
    $("log-titulo").textContent = `Detalhes do erro · ${it ? it.nome : ""}`;
    $("log-texto").textContent = texto || "Sem detalhes de erro para este arquivo.";
    $("modal-log").classList.remove("oculto");
    $("log-ok").focus();
  }
  const fecharLog = () => $("modal-log").classList.add("oculto");

  /* ---------- acoes ---------- */
  async function converter() {
    if (s.convertendo) return;
    fecharAviso();
    // garante que largura/altura digitadas foram enviadas antes de converter
    await api.definir_opcoes({ raw_in_largura: $("raw-largura").value, raw_in_altura: $("raw-altura").value });
    const erro = await api.converter();
    if (erro) aviso(erro.titulo, erro.texto);
  }
  function cancelar() {
    if (!s.convertendo || s.cancelando) return;
    s.cancelando = true;
    $("geral-rotulo").textContent = "Cancelando… (terminando as que já começaram)";
    atualizarControles();
    api.cancelar();
  }

  /* ---------- eventos do Python ---------- */
  function receber(ev) {
    switch (ev.tipo) {
      case "fila":
        s.itens = ev.itens;
        s.resumo = ev.resumo;
        s.destino = ev.destino;
        s.convertendo = ev.convertendo;
        if (!s.convertendo) s.cancelando = false;
        $("painel-raw").classList.toggle("oculto", !ev.raw_sem_nome);
        desenharFila();
        atualizarControles();
        break;
      case "item": {
        const i = s.itens.findIndex((x) => x.path === ev.path);
        if (i >= 0) { s.itens[i].status = ev.status; pintarItem(i); }
        break;
      }
      case "inicio":
        s.convertendo = true;
        s.cancelando = false;
        $("geral-rotulo").textContent = `Convertendo · 0 de ${ev.total} prontas`;
        geral(0);
        atualizarControles();
        break;
      case "progresso":
        geral(ev.prontas / ev.total);
        if (!s.cancelando) $("geral-rotulo").textContent = `Convertendo · ${ev.prontas} de ${ev.total} prontas`;
        break;
      case "fim":
        s.convertendo = false;
        s.cancelando = false;
        s.lotes += 1;
        atualizarControles();
        DS.toast(ev.resumo, s.destino.caminho && ev.feitos ? { acao: () => api.abrir_pasta(), rotulo: "Abrir pasta", duracao: 7000 } : { duracao: 5000 });
        break;
      case "aviso":
        aviso(ev.titulo, ev.texto);
        break;
    }
  }
  function geral(fracao) {
    $("geral-pct").textContent = `${Math.floor((fracao || 0) * 100)}%`;
    $("geral-barra").firstElementChild.style.width = `${(fracao || 0) * 100}%`;
  }

  /* ---------- arrastar e soltar ---------- */
  function ativarArrastar() {
    let profundidade = 0;
    const mostrar = (sim) => $("soltar-tela").classList.toggle("oculto", !sim);
    const temArquivos = (e) => e.dataTransfer && [...(e.dataTransfer.types || [])].includes("Files");
    document.addEventListener("dragenter", (e) => { if (!temArquivos(e) || s.convertendo) return; e.preventDefault(); profundidade++; mostrar(true); });
    document.addEventListener("dragover", (e) => { if (temArquivos(e)) e.preventDefault(); });
    document.addEventListener("dragleave", () => { profundidade = Math.max(0, profundidade - 1); if (!profundidade) mostrar(false); });
    // o Python recebe o caminho completo do pywebview e adiciona os arquivos
    document.addEventListener("drop", (e) => { e.preventDefault(); profundidade = 0; mostrar(false); });
  }

  /* ---------- inicio ---------- */
  async function iniciar() {
    desenharIcones();
    api = await DS.api();
    const ini = await api.estado_inicial();
    s.comPerda = ini.com_perda;
    $("formatos").innerHTML = ini.formatos.map((f) => `<button data-f="${f}" aria-pressed="false">${f.toUpperCase()}</button>`).join("");
    const opcoesPix = ini.pix_fmts.map((p) => `<option>${p}</option>`).join("");
    $("raw-pix").innerHTML = opcoesPix;
    $("raw-saida-pix").innerHTML = opcoesPix;
    const o = ini.opcoes;
    $("raw-largura").value = o.raw_in_largura;
    $("raw-altura").value = o.raw_in_altura;
    $("raw-pix").value = o.raw_in_pix_fmt;
    $("raw-saida-pix").value = o.raw_out_pix_fmt;
    $("qualidade").value = o.qualidade;
    $("qualidade-valor").textContent = o.qualidade;
    mostrarFormato(o.formato);
    receber({ tipo: "fila", itens: [], resumo: s.resumo, destino: ini.destino, raw_sem_nome: false, convertendo: false });

    $("btn-adicionar").onclick = () => api.escolher_arquivos();
    $("soltar").onclick = () => api.escolher_arquivos();
    $("btn-converter").onclick = converter;
    $("btn-cancelar").onclick = cancelar;
    $("btn-trocar").onclick = () => api.trocar_pasta();
    $("btn-padrao").onclick = () => api.usar_pasta_padrao();
    $("btn-abrir-pasta").onclick = () => api.abrir_pasta();
    $("aviso-fechar").onclick = fecharAviso;
    $("formatos").onclick = (e) => {
      const b = e.target.closest("button[data-f]");
      if (!b || s.convertendo) return;
      mostrarFormato(b.dataset.f);
      mudarOpcao("formato", b.dataset.f);
    };
    $("qualidade").oninput = () => { $("qualidade-valor").textContent = $("qualidade").value; };
    $("qualidade").onchange = () => mudarOpcao("qualidade", Number($("qualidade").value));
    $("raw-pix").onchange = () => mudarOpcao("raw_in_pix_fmt", $("raw-pix").value);
    $("raw-saida-pix").onchange = () => mudarOpcao("raw_out_pix_fmt", $("raw-saida-pix").value);
    $("raw-largura").onchange = () => mudarOpcao("raw_in_largura", $("raw-largura").value);
    $("raw-altura").onchange = () => mudarOpcao("raw_in_altura", $("raw-altura").value);
    $("lista").onclick = (e) => {
      const linha = e.target.closest(".item");
      if (!linha) return;
      const it = s.itens[Number(linha.dataset.i)];
      if (e.target.closest(".remover") && !s.convertendo) api.remover(it.path);
      else if (e.target.closest(".ver-log")) verLog(it.path);
    };
    $("log-fechar").onclick = $("log-ok").onclick = fecharLog;
    $("modal-log").onclick = (e) => { if (e.target === $("modal-log")) fecharLog(); };
    $("log-copiar").onclick = async () => {
      try { await navigator.clipboard.writeText($("log-texto").textContent); DS.toast("Detalhes copiados"); }
      catch (_) { DS.toast("Selecione o texto e copie com Ctrl+C"); }
    };
    document.addEventListener("keydown", (e) => {
      if (e.key !== "Escape") return;
      if (!$("modal-log").classList.contains("oculto")) fecharLog();
      else if (s.convertendo) cancelar();
      else fecharAviso();
    });
    ativarArrastar();
    document.documentElement.classList.add("pronto");
  }

  document.addEventListener("DOMContentLoaded", iniciar);
  return { receber, _s: s };
})();
