const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const puppeteer = require('puppeteer');

/**
 * Minerador de Acórdãos do TCE-AM.
 *
 * IMPORTANTE:
 * Como o HTML real pode mudar, todos os seletores ficam centralizados aqui.
 * Abra o DevTools do navegador, inspecione os campos/botões e ajuste apenas
 * este objeto quando necessário.
 */
const DATA_INICIAL_BR = process.env.DATA_INICIAL || '01/01/2023';
const DATA_FINAL_BR = process.env.DATA_FINAL || formatarDataBR(new Date());
const DATA_INICIAL_API = normalizarDataApi(DATA_INICIAL_BR);
const DATA_FINAL_API = normalizarDataApi(DATA_FINAL_BR);

const CONFIG = {
  url: 'https://jurisprudencia.tce.am.gov.br/',
  apiUrl: 'https://jurisprudencia-api.tce.am.gov.br/decisorios/byManyFields',
  termoBusca: '"multa" AND "secretaria"',
  dataInicial: DATA_INICIAL_BR,
  dataFinal: DATA_FINAL_BR,
  dataInicialApi: DATA_INICIAL_API,
  dataFinalApi: DATA_FINAL_API,
  usarApiDireta: true,
  particionarPorMes: process.env.PARTICIONAR_POR_MES ? process.env.PARTICIONAR_POR_MES === 'true' : true,
  baixarPdfs: process.env.NO_DOWNLOAD !== 'true',
  pastaDownloads: path.resolve(
    __dirname,
    'pdfs_baixados',
    `sessao_${DATA_INICIAL_API}_${DATA_FINAL_API}`,
  ),
  headless: process.env.HEADLESS ? process.env.HEADLESS === 'true' : false,
  maxPaginas: Number(process.env.MAX_PAGINAS || '0'), // 0 = sem limite. Use 1, 2, 3... para debugar.
  maxDownloads: Number(process.env.MAX_DOWNLOADS || '0'), // 0 = sem limite. Use 10, 20... para testar.
  downloadConcurrency: Number(process.env.DOWNLOAD_CONCURRENCY || '8'),
  delays: {
    min: Number(process.env.DELAY_MIN_MS || '1000'),
    max: Number(process.env.DELAY_MAX_MS || '3000'),
  },
  timeout: {
    pagina: 60000,
    seletor: 20000,
    download: 45000,
    api: 45000,
  },
  selectors: {
    // Campo principal de busca. Ajuste para o seletor exato do site se preciso.
    campoBusca: [
      '#inputConteudo',
      'input[type="search"]',
      'input[name*="pesquisa" i]',
      'input[name*="busca" i]',
      'input[name*="termo" i]',
      'input[id*="pesquisa" i]',
      'input[id*="busca" i]',
      'textarea[name*="pesquisa" i]',
      'textarea[name*="busca" i]',
    ],

    // Filtros de data inicial/final.
    dataInicial: [
      'span.p-calendar input.p-inputtext',
      'input[name*="dataInicial" i]',
      'input[name*="dtInicial" i]',
      'input[name*="inicio" i]',
      'input[id*="dataInicial" i]',
      'input[id*="dtInicial" i]',
      'input[id*="inicio" i]',
      'input[placeholder*="inicial" i]',
      'input[aria-label*="inicial" i]',
    ],
    dataFinal: [
      'span.p-calendar input.p-inputtext',
      'input[name*="dataFinal" i]',
      'input[name*="dtFinal" i]',
      'input[name*="fim" i]',
      'input[id*="dataFinal" i]',
      'input[id*="dtFinal" i]',
      'input[id*="fim" i]',
      'input[placeholder*="final" i]',
      'input[aria-label*="final" i]',
    ],

    // Botão de pesquisa.
    botaoPesquisar: [
      'button.p-button-success',
      'button[type="submit"]',
      'input[type="submit"]',
      'button[id*="pesquisar" i]',
      'button[id*="buscar" i]',
      'button[class*="pesquisar" i]',
      'button[class*="buscar" i]',
      'a[id*="pesquisar" i]',
      'a[class*="pesquisar" i]',
    ],

    // Algum elemento que indique que a lista carregou.
    resultados: [
      'button.p-button-sm',
      '.p-paginator',
      '[class*="p-dataview" i]',
      'table tbody tr',
      '.table tbody tr',
      '[role="table"] [role="row"]',
      '.resultado',
      '.resultados',
      '[class*="resultado" i]',
    ],

    // Elementos candidatos a PDF. A filtragem fina é feita por texto/href/onclick.
    candidatosPdf: 'a, button',

    // Botão/link de próxima página.
    proximaPagina: [
      'button.p-paginator-next',
      'a[rel="next"]',
      'button[aria-label*="próxima" i]',
      'button[aria-label*="proxima" i]',
      'a[aria-label*="próxima" i]',
      'a[aria-label*="proxima" i]',
      '.pagination .next a',
      '.pagination .next button',
      '.pagination-next a',
      '.pagination-next button',
      'li.next a',
      'li.next button',
      'a.page-link',
      'button.page-link',
    ],
  },
};

async function main() {
  garantirDiretorio(CONFIG.pastaDownloads);

  const browser = await puppeteer.launch({
    headless: CONFIG.headless,
    defaultViewport: null,
    args: ['--start-maximized'],
  });

  const page = await browser.newPage();
  page.setDefaultTimeout(CONFIG.timeout.seletor);
  page.setDefaultNavigationTimeout(CONFIG.timeout.pagina);

  await habilitarDownloads(page, CONFIG.pastaDownloads);

  try {
    console.log(`[INFO] Acessando ${CONFIG.url}`);
    await page.goto(CONFIG.url, { waitUntil: 'networkidle2' });

    if (CONFIG.usarApiDireta) {
      await minerarViaApiDireta(page);
      return;
    }

    await aguardarFormulario(page);
    await preencherFormulario(page);
    await submeterPesquisa(page);

    let paginaAtual = 1;
    const urlsBaixadas = new Set();

    while (true) {
      console.log(`\n[INFO] Processando página ${paginaAtual}`);
      await aguardarResultados(page);

      const candidatos = await coletarCandidatosPdf(page);
      console.log(`[INFO] ${candidatos.length} candidato(s) a PDF encontrado(s).`);

      for (let i = 0; i < candidatos.length; i += 1) {
        const candidato = candidatos[i];
        const chave = candidato.href || `${candidato.domIndex}:${candidato.texto}:${candidato.linha}`;

        if (urlsBaixadas.has(chave)) {
          continue;
        }

        try {
          const arquivo = await baixarPdfSeguro(page, browser, candidato, paginaAtual, i + 1);
          urlsBaixadas.add(chave);
          console.log(`[OK] PDF salvo: ${arquivo}`);
          await delayAleatorio();
        } catch (error) {
          console.error(`[ERRO] Falha ao baixar PDF ${i + 1} da página ${paginaAtual}: ${error.message}`);
        }
      }

      if (CONFIG.maxPaginas > 0 && paginaAtual >= CONFIG.maxPaginas) {
        console.log('[INFO] Limite de páginas atingido.');
        break;
      }

      const avancou = await irParaProximaPagina(page);
      if (!avancou) {
        console.log('[INFO] Não há próxima página habilitada. Encerrando.');
        break;
      }

      paginaAtual += 1;
      await delayAleatorio();
    }
  } catch (error) {
    console.error(`[FATAL] ${error.stack || error.message}`);
  } finally {
    console.log('[INFO] Execução finalizada. Navegador mantido aberto por 5 segundos.');
    await sleep(5000);
    await browser.close();
  }
}

async function minerarViaApiDireta(page) {
  console.log('[INFO] Usando API direta do TCE-AM com filtro real de Data Sessão.');
  console.log(`[INFO] Janela de datas: ${CONFIG.dataInicial} (${CONFIG.dataInicialApi}) até ${CONFIG.dataFinal} (${CONFIG.dataFinalApi})`);

  const janelas = CONFIG.particionarPorMes
    ? gerarJanelasMensais(CONFIG.dataInicialApi, CONFIG.dataFinalApi)
    : [{ inicioApi: CONFIG.dataInicialApi, fimApi: CONFIG.dataFinalApi, label: 'periodo_completo' }];

  console.log(`[INFO] Janelas de busca: ${janelas.length}`);

  const registrosPorJanela = [];
  const janelasNoLimite = [];

  for (const janela of janelas) {
    const registrosJanela = await buscarRegistrosApi(janela.inicioApi, janela.fimApi);
    const registrosFiltrados = filtrarRegistrosPorData(registrosJanela, janela.inicioApi, janela.fimApi)
      .map((registro) => ({
        ...registro,
        janelaBusca: janela.label,
      }));

    console.log(`[INFO] ${janela.label}: API=${registrosJanela.length}, dentro_data=${registrosFiltrados.length}`);

    if (registrosJanela.length >= 1000) {
      janelasNoLimite.push(janela);
    }

    registrosPorJanela.push(...registrosFiltrados);
    await delayAleatorio();
  }

  const registrosUnicos = deduplicarRegistros(registrosPorJanela);

  console.log(`[INFO] Registros somados nas janelas: ${registrosPorJanela.length}`);
  console.log(`[INFO] Registros únicos por PDF/processo: ${registrosUnicos.length}`);

  if (janelasNoLimite.length > 0) {
    console.warn(`[AVISO] ${janelasNoLimite.length} janela(s) bateram 1000 resultados. Próximo refinamento: quebrar essas janelas por dia.`);
    console.warn(`[AVISO] Janelas no limite: ${janelasNoLimite.map((janela) => janela.label).join(', ')}`);
  }

  salvarMetadata(registrosUnicos, janelas);

  if (!CONFIG.baixarPdfs) {
    console.log('[INFO] NO_DOWNLOAD=true; somente metadata foi gerada.');
    return;
  }

  await baixarRegistrosApiDireto(registrosUnicos);
}

async function buscarRegistrosApi(inicioApi = CONFIG.dataInicialApi, fimApi = CONFIG.dataFinalApi) {
  const params = new URLSearchParams({
    strConteudo: CONFIG.termoBusca,
    strAnoProcesso: 'null',
    strNumeroProcesso: 'null',
    strAnoDecisorio: 'null',
    strNumeroDecisorio: 'null',
    strRelatorDecisorio: 'null',
    strOrgaoJulgador: 'null',
    strDataSessaoInicio: inicioApi,
    strDataSessaoFim: fimApi,
    strNatureza: 'null',
  });

  const url = `${CONFIG.apiUrl}?${params.toString()}`;
  let response;
  for (let tentativa = 1; tentativa <= 3; tentativa += 1) {
    try {
      response = await fetchComTimeout(url, {
        headers: {
          Accept: 'application/json',
          'User-Agent': 'gov-miner-tce-am/1.0',
        },
      }, CONFIG.timeout.api);
      break;
    } catch (error) {
      if (tentativa === 3) {
        throw error;
      }
      console.warn(`[AVISO] Falha na API (${tentativa}/3). Tentando novamente: ${error.message}`);
      await sleep(1000 * tentativa);
    }
  }

  if (!response.ok) {
    throw new Error(`API retornou HTTP ${response.status}: ${url}`);
  }

  const data = await response.json();
  if (!Array.isArray(data)) {
    throw new Error('API não retornou uma lista de decisórios.');
  }

  return data;
}

function filtrarRegistrosPorData(registros, inicioApi = CONFIG.dataInicialApi, fimApi = CONFIG.dataFinalApi) {
  const inicio = Number(inicioApi);
  const fim = Number(fimApi);

  return registros.filter((registro) => {
    const dataSessao = Number(String(registro.dataSessao || '').replace(/\D/g, ''));
    return dataSessao >= inicio && dataSessao <= fim;
  });
}

function deduplicarRegistros(registros) {
  const porChave = new Map();

  for (const registro of registros) {
    const chave = registro.caminhoArquivo
      || `${registro.numeroProcesso || ''}/${registro.anoProcesso || ''}_${registro.numeroDecisorio || ''}/${registro.anoDecisorio || ''}`;

    if (!chave.trim()) {
      continue;
    }

    if (!porChave.has(chave)) {
      porChave.set(chave, registro);
      continue;
    }

    const atual = porChave.get(chave);
    porChave.set(chave, {
      ...atual,
      ...registro,
      janelaBusca: [atual.janelaBusca, registro.janelaBusca].filter(Boolean).join('|'),
    });
  }

  return Array.from(porChave.values()).sort((a, b) => {
    const dataA = String(a.dataSessao || '');
    const dataB = String(b.dataSessao || '');
    if (dataA !== dataB) return dataA.localeCompare(dataB);
    return String(a.caminhoArquivo || '').localeCompare(String(b.caminhoArquivo || ''));
  });
}

async function baixarRegistrosApiDireto(registros) {
  const registrosComUrl = registros.filter((registro) => Boolean(registro.caminhoArquivo));
  const limite = CONFIG.maxDownloads > 0
    ? Math.min(CONFIG.maxDownloads, registrosComUrl.length)
    : registrosComUrl.length;
  const fila = registrosComUrl.slice(0, limite);
  const concorrencia = Math.max(1, CONFIG.downloadConcurrency);

  let proximoIndice = 0;
  let baixados = 0;
  let existentes = 0;
  let falhas = 0;

  console.log(`[INFO] Downloads a processar: ${fila.length}`);
  console.log(`[INFO] Concorrência de download: ${concorrencia}`);

  async function worker(workerId) {
    while (true) {
      const indice = proximoIndice;
      proximoIndice += 1;

      if (indice >= fila.length) {
        return;
      }

      const registro = fila[indice];
      const candidato = candidatoFromRegistro(registro);

      try {
        const resultado = await baixarPdfPorUrlDireto(registro.caminhoArquivo, candidato, 1, indice + 1);
        if (resultado.status === 'exists') {
          existentes += 1;
        } else {
          baixados += 1;
        }

        const totalProcessado = baixados + existentes + falhas;
        if (totalProcessado % 50 === 0 || resultado.status === 'downloaded') {
          console.log(`[OK][W${workerId}] ${resultado.status}: ${resultado.arquivo}`);
        }
      } catch (error) {
        falhas += 1;
        console.error(`[ERRO][W${workerId}] Falha no registro ${indice + 1}: ${error.message}`);
      }
    }
  }

  await Promise.all(
    Array.from({ length: concorrencia }, (_, index) => worker(index + 1)),
  );

  console.log(`[INFO] PDFs novos baixados nesta execução: ${baixados}`);
  console.log(`[INFO] PDFs já existentes reaproveitados: ${existentes}`);
  console.log(`[INFO] Falhas de download: ${falhas}`);
}

async function baixarPdfPorUrlDireto(url, candidato, paginaAtual, indiceNaPagina) {
  const arquivo = montarCaminhoArquivo(candidato, paginaAtual, indiceNaPagina, url);
  if (fs.existsSync(arquivo) && fs.statSync(arquivo).size > 0) {
    return { status: 'exists', arquivo };
  }

  const response = await fetchComTimeout(url, {
    headers: {
      Accept: 'application/pdf,application/octet-stream,*/*',
      'User-Agent': 'gov-miner-tce-am/1.0',
    },
  }, CONFIG.timeout.download);

  if (!response.ok) {
    throw new Error(`HTTP ${response.status} ao baixar ${url}`);
  }

  const contentType = response.headers.get('content-type') || '';
  const buffer = Buffer.from(await response.arrayBuffer());
  if (buffer.length === 0) {
    throw new Error(`Resposta vazia ao baixar ${url}`);
  }

  const inicio = buffer.subarray(0, 20).toString('latin1');
  const parecePdf = contentType.includes('pdf') || inicio.includes('%PDF');
  const pareceHtml = contentType.includes('html') || /^<!doctype html|^<html/i.test(inicio.trim());

  if (!parecePdf && pareceHtml) {
    throw new Error(`URL retornou HTML em vez de PDF: ${url}`);
  }

  fs.writeFileSync(arquivo, buffer);
  return { status: 'downloaded', arquivo };
}

function candidatoFromRegistro(registro) {
  const linha = [
    `Data Sessão: ${registro.dataSessao || 'sem-data'}`,
    `Processo: ${registro.numeroProcesso || 'sem-numero'}/${registro.anoProcesso || 'sem-ano'}`,
    `Decisório: ${registro.numeroDecisorio || 'sem-numero'}/${registro.anoDecisorio || 'sem-ano'}`,
    `Natureza: ${registro.natureza || 'sem-natureza'}`,
    `Órgão: ${registro.orgao || 'sem-orgao'}`,
  ].join(' ');

  return {
    href: registro.caminhoArquivo || null,
    texto: registro.nomeDoArquivo || '',
    titulo: registro.tipoDecisorio || '',
    aria: '',
    linha,
  };
}

function salvarMetadata(registros, janelas = []) {
  const registrosComArquivos = registros.map((registro, index) => {
    const candidato = candidatoFromRegistro(registro);
    return {
      dataSessao: registro.dataSessao || null,
      numeroProcesso: registro.numeroProcesso || null,
      anoProcesso: registro.anoProcesso || null,
      numeroDecisorio: registro.numeroDecisorio || null,
      anoDecisorio: registro.anoDecisorio || null,
      natureza: registro.natureza || null,
      orgao: registro.orgao || null,
      partes: registro.partes || null,
      objeto: registro.objeto || null,
      interessados: registro.interessados || null,
      relatorVotoDecisao: registro.relatorVotoDecisao || null,
      tipoDecisorio: registro.tipoDecisorio || null,
      caminhoArquivo: registro.caminhoArquivo || null,
      nomeDoArquivo: registro.nomeDoArquivo || null,
      janelaBusca: registro.janelaBusca || null,
      pdfLocal: registro.caminhoArquivo
        ? montarCaminhoArquivo(candidato, 1, index + 1, registro.caminhoArquivo)
        : null,
    };
  });

  const metadataPath = path.join(
    CONFIG.pastaDownloads,
    `metadata_${CONFIG.dataInicialApi}_${CONFIG.dataFinalApi}.jsonl`,
  );
  const masterJsonlPath = path.join(
    CONFIG.pastaDownloads,
    `metadata_master_${CONFIG.dataInicialApi}_${CONFIG.dataFinalApi}.jsonl`,
  );
  const masterCsvPath = path.join(
    CONFIG.pastaDownloads,
    `metadata_master_${CONFIG.dataInicialApi}_${CONFIG.dataFinalApi}.csv`,
  );
  const janelasPath = path.join(
    CONFIG.pastaDownloads,
    `janelas_busca_${CONFIG.dataInicialApi}_${CONFIG.dataFinalApi}.json`,
  );

  const linhas = registrosComArquivos.map((registro) => JSON.stringify(registro, null, 0));

  fs.writeFileSync(metadataPath, `${linhas.join('\n')}\n`, 'utf-8');
  fs.writeFileSync(masterJsonlPath, `${linhas.join('\n')}\n`, 'utf-8');
  fs.writeFileSync(masterCsvPath, montarCsv(registrosComArquivos), 'utf-8');
  fs.writeFileSync(janelasPath, JSON.stringify(janelas, null, 2), 'utf-8');

  console.log(`[INFO] Metadata salva: ${metadataPath}`);
  console.log(`[INFO] Metadata master JSONL: ${masterJsonlPath}`);
  console.log(`[INFO] Metadata master CSV: ${masterCsvPath}`);
}

async function aguardarFormulario(page) {
  const campo = await encontrarPrimeiroVisivel(page, CONFIG.selectors.campoBusca, CONFIG.timeout.seletor);
  if (!campo) {
    throw new Error('Campo principal de busca não encontrado. Ajuste CONFIG.selectors.campoBusca.');
  }
  await campo.handle.dispose();
}

async function preencherFormulario(page) {
  await preencherCampo(page, CONFIG.selectors.campoBusca, CONFIG.termoBusca, 'campo de busca');

  let preencheuDataInicial = await preencherDataPrimeVue(page, 0, CONFIG.dataInicial, 'data inicial');
  let preencheuDataFinal = await preencherDataPrimeVue(page, 1, CONFIG.dataFinal, 'data final');

  if (!preencheuDataInicial) {
    preencheuDataInicial = await preencherCampo(
      page,
      CONFIG.selectors.dataInicial,
      CONFIG.dataInicial,
      'data inicial',
      false,
    );
  }

  if (!preencheuDataFinal) {
    preencheuDataFinal = await preencherCampo(
      page,
      CONFIG.selectors.dataFinal,
      CONFIG.dataFinal,
      'data final',
      false,
    );
  }

  if (!preencheuDataInicial || !preencheuDataFinal) {
    console.warn('[AVISO] Algum campo de data não foi encontrado. Ajuste os seletores de data se o filtro for obrigatório.');
  }
}

async function submeterPesquisa(page) {
  const botao = await encontrarPrimeiroVisivel(page, CONFIG.selectors.botaoPesquisar, 5000);

  if (botao) {
    console.log(`[INFO] Clicando no botão de pesquisa: ${botao.selector}`);
    await Promise.allSettled([
      page.waitForNavigation({ waitUntil: 'networkidle2', timeout: CONFIG.timeout.pagina }),
      botao.handle.click(),
    ]);
    await botao.handle.dispose();
  } else {
    console.warn('[AVISO] Botão de pesquisa não encontrado. Enviando Enter no campo de busca.');
    const campo = await encontrarPrimeiroVisivel(page, CONFIG.selectors.campoBusca, CONFIG.timeout.seletor);
    if (!campo) {
      throw new Error('Não foi possível submeter: campo de busca também não foi reencontrado.');
    }
    await Promise.allSettled([
      page.waitForNavigation({ waitUntil: 'networkidle2', timeout: CONFIG.timeout.pagina }),
      campo.handle.press('Enter'),
    ]);
    await campo.handle.dispose();
  }

  await esperarRedeAcalmar(page);
}

async function aguardarResultados(page) {
  const resultado = await encontrarPrimeiroVisivel(page, CONFIG.selectors.resultados, CONFIG.timeout.seletor);
  if (resultado) {
    await resultado.handle.dispose();
    return;
  }
  console.warn('[AVISO] Tabela/lista de resultados não encontrada pelos seletores atuais. Tentando continuar mesmo assim.');
}

async function coletarCandidatosPdf(page) {
  return page.evaluate((selector) => {
    const elementos = Array.from(document.querySelectorAll(selector));

    function textoLimpo(valor) {
      return (valor || '').replace(/\s+/g, ' ').trim();
    }

    function absoluto(href) {
      if (!href) return null;
      try {
        return new URL(href, window.location.href).href;
      } catch {
        return null;
      }
    }

    function textoDoResultado(el) {
      let atual = el;
      for (let profundidade = 0; atual && profundidade < 10; profundidade += 1) {
        if (atual === document.body || atual === document.documentElement) {
          break;
        }
        const texto = textoLimpo(atual.innerText || atual.textContent);
        if (/Processo:|[ÓO]rg[aã]o:|Objeto:/i.test(texto) && texto.length > 30) {
          return texto;
        }
        atual = atual.parentElement;
      }
      return textoLimpo(el.closest('tr')?.innerText || el.closest('[role="row"]')?.innerText || '');
    }

    function parecePdf(el, href, textoComposto) {
      return (
        /\.pdf(?:[?#].*)?$/i.test(href || '') ||
        /pdf|download|baixar|visualizar|inteiro\s+teor|ac[oó]rd[aã]o|arquivo/i.test(textoComposto || '') ||
        /pdf|download|baixar|acordao|acórdão/i.test(el.getAttribute('onclick') || '')
      );
    }

    return elementos
      .map((el, domIndex) => {
        const href = absoluto(el.href || el.getAttribute('href'));
        const texto = textoLimpo(el.innerText || el.textContent);
        const titulo = textoLimpo(el.getAttribute('title'));
        const aria = textoLimpo(el.getAttribute('aria-label'));
        const classe = textoLimpo(String(el.className || ''));
        const linha = textoDoResultado(el);
        const textoComposto = `${href || ''} ${texto} ${titulo} ${aria} ${classe}`;

        return {
          domIndex,
          href,
          texto,
          titulo,
          aria,
          linha,
          parecePdf: parecePdf(el, href, textoComposto),
        };
      })
      .filter((item) => item.parecePdf);
  }, CONFIG.selectors.candidatosPdf);
}

async function baixarPdfSeguro(page, browser, candidato, paginaAtual, indiceNaPagina) {
  if (candidato.href) {
    try {
      return await baixarPdfPorUrl(page, candidato.href, candidato, paginaAtual, indiceNaPagina);
    } catch (error) {
      console.warn(`[AVISO] Download direto falhou; tentando clique no elemento. Motivo: ${error.message}`);
    }
  }

  return baixarPdfPorClique(page, browser, candidato, paginaAtual, indiceNaPagina);
}

async function baixarPdfPorUrl(page, url, candidato, paginaAtual, indiceNaPagina) {
  const arquivo = montarCaminhoArquivo(candidato, paginaAtual, indiceNaPagina, url);
  if (fs.existsSync(arquivo) && fs.statSync(arquivo).size > 0) {
    return arquivo;
  }

  const cookies = await page.cookies(url);
  const cookieHeader = cookies.map((cookie) => `${cookie.name}=${cookie.value}`).join('; ');
  const userAgent = await page.evaluate(() => navigator.userAgent);

  const response = await fetch(url, {
    headers: {
      Cookie: cookieHeader,
      Referer: page.url(),
      'User-Agent': userAgent,
      Accept: 'application/pdf,application/octet-stream,*/*',
    },
  });

  if (!response.ok) {
    throw new Error(`HTTP ${response.status} ao baixar ${url}`);
  }

  const contentType = response.headers.get('content-type') || '';
  const buffer = Buffer.from(await response.arrayBuffer());
  if (buffer.length === 0) {
    throw new Error(`Resposta vazia ao baixar ${url}`);
  }

  const inicio = buffer.subarray(0, 20).toString('latin1');
  const parecePdf = contentType.includes('pdf') || inicio.includes('%PDF');
  const pareceHtml = contentType.includes('html') || /^<!doctype html|^<html/i.test(inicio.trim());

  if (!parecePdf && pareceHtml) {
    throw new Error(`URL retornou HTML em vez de PDF: ${url}`);
  }

  fs.writeFileSync(arquivo, buffer);
  return arquivo;
}

async function baixarPdfPorClique(page, browser, candidato, paginaAtual, indiceNaPagina) {
  const arquivosAntes = listarArquivos(CONFIG.pastaDownloads);
  const popupPromise = aguardarNovaAba(browser, 10000);

  const clicou = await page.evaluate((selector, domIndex) => {
    const elemento = Array.from(document.querySelectorAll(selector))[domIndex];
    if (!elemento) return false;
    elemento.scrollIntoView({ block: 'center', inline: 'center' });
    elemento.click();
    return true;
  }, CONFIG.selectors.candidatosPdf, candidato.domIndex);

  if (!clicou) {
    throw new Error('Elemento de download não encontrado para clique.');
  }

  const novaAba = await popupPromise;
  if (novaAba) {
    await novaAba.bringToFront().catch(() => {});
    await esperarRedeAcalmar(novaAba);
    const url = novaAba.url();
    await novaAba.close().catch(() => {});

    if (url && url !== 'about:blank') {
      return baixarPdfPorUrl(page, url, candidato, paginaAtual, indiceNaPagina);
    }
  }

  const arquivoBaixado = await aguardarArquivoNovo(CONFIG.pastaDownloads, arquivosAntes, CONFIG.timeout.download);
  if (!arquivoBaixado) {
    throw new Error('Clique executado, mas nenhum arquivo novo apareceu na pasta de downloads.');
  }

  const destino = montarCaminhoArquivo(candidato, paginaAtual, indiceNaPagina, arquivoBaixado);
  if (fs.existsSync(destino) && fs.statSync(destino).size > 0) {
    fs.unlinkSync(arquivoBaixado);
    return destino;
  }

  fs.renameSync(arquivoBaixado, destino);
  return destino;
}

async function irParaProximaPagina(page) {
  const assinaturaAntes = await assinaturaResultados(page);
  const proximo = await encontrarProximaPagina(page);

  if (!proximo || proximo.disabled) {
    return false;
  }

  console.log(`[INFO] Indo para próxima página via seletor: ${proximo.selector}`);

  await Promise.allSettled([
    page.waitForNavigation({ waitUntil: 'networkidle2', timeout: CONFIG.timeout.pagina }),
    page.evaluate((selector, index) => {
      const elementos = Array.from(document.querySelectorAll(selector));
      const elemento = elementos[index];
      if (!elemento) return false;
      elemento.scrollIntoView({ block: 'center', inline: 'center' });
      elemento.click();
      return true;
    }, proximo.selector, proximo.index),
  ]);

  await esperarRedeAcalmar(page);

  try {
    await page.waitForFunction(
      (assinatura) => document.body.innerText.slice(0, 4000) !== assinatura,
      { timeout: 10000 },
      assinaturaAntes,
    );
  } catch {
    console.warn('[AVISO] A página não mudou claramente após clicar em próxima. Continuando com cautela.');
  }

  return true;
}

async function encontrarProximaPagina(page) {
  return page.evaluate((selectors) => {
    function visivel(el) {
      const style = window.getComputedStyle(el);
      const rect = el.getBoundingClientRect();
      return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
    }

    function desabilitado(el) {
      const parent = el.closest('li, .page-item, .pagination-item');
      const className = `${el.className || ''} ${parent?.className || ''}`;
      return (
        el.disabled ||
        el.getAttribute('aria-disabled') === 'true' ||
        /disabled|desabilitado/i.test(className)
      );
    }

    for (const selector of selectors) {
      const elementos = Array.from(document.querySelectorAll(selector));
      for (let index = 0; index < elementos.length; index += 1) {
        const el = elementos[index];
        const texto = (el.innerText || el.textContent || el.getAttribute('aria-label') || '').trim();
        const aria = el.getAttribute('aria-label') || '';
        const rel = el.getAttribute('rel') || '';
        const className = `${el.className || ''} ${el.closest('li, .page-item, .pagination-item')?.className || ''}`;
        const composto = `${texto} ${aria} ${rel} ${className}`;

        if (!visivel(el)) continue;
        if (!/pr[oó]xima|proxima|next|p-paginator-next|>|»/i.test(composto)) continue;

        return {
          selector,
          index,
          disabled: desabilitado(el),
          label: texto || aria || rel,
        };
      }
    }

    return null;
  }, CONFIG.selectors.proximaPagina);
}

async function preencherCampo(page, selectors, valor, nomeCampo, obrigatorio = true) {
  const encontrado = await encontrarPrimeiroVisivel(page, selectors, obrigatorio ? CONFIG.timeout.seletor : 3000);
  if (!encontrado) {
    if (obrigatorio) {
      throw new Error(`Campo obrigatório não encontrado: ${nomeCampo}`);
    }
    return false;
  }

  const valorAjustado = await encontrado.handle.evaluate((el, valorBr) => {
    if (el instanceof HTMLInputElement && el.type === 'date') {
      const [dia, mes, ano] = valorBr.split('/');
      return `${ano}-${mes}-${dia}`;
    }
    return valorBr;
  }, valor);

  console.log(`[INFO] Preenchendo ${nomeCampo}: ${valor}`);
  await encontrado.handle.evaluate((el, novoValor) => {
    el.focus();

    if (el.isContentEditable) {
      el.textContent = novoValor;
    } else {
      const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
      const descriptor = Object.getOwnPropertyDescriptor(proto, 'value');
      if (descriptor && descriptor.set) {
        descriptor.set.call(el, novoValor);
      } else {
        el.value = novoValor;
      }
    }

    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
    el.blur();
  }, valorAjustado);

  await encontrado.handle.dispose();
  return true;
}

async function preencherDataPrimeVue(page, indice, valor, nomeCampo) {
  const preenchido = await page.evaluate((indiceAlvo, novoValor) => {
    const campos = Array.from(document.querySelectorAll('span.p-calendar input.p-inputtext'))
      .filter((el) => {
        const style = window.getComputedStyle(el);
        const rect = el.getBoundingClientRect();
        return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
      });

    const campo = campos[indiceAlvo];
    if (!campo) return false;

    campo.focus();
    const descriptor = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value');
    if (descriptor && descriptor.set) {
      descriptor.set.call(campo, novoValor);
    } else {
      campo.value = novoValor;
    }
    campo.dispatchEvent(new Event('input', { bubbles: true }));
    campo.dispatchEvent(new Event('change', { bubbles: true }));
    campo.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    campo.blur();
    return true;
  }, indice, valor);

  if (preenchido) {
    console.log(`[INFO] Preenchendo ${nomeCampo}: ${valor}`);
  }

  return preenchido;
}

async function encontrarPrimeiroVisivel(page, selectors, timeoutMs) {
  const deadline = Date.now() + timeoutMs;

  while (Date.now() < deadline) {
    for (const selector of selectors) {
      const handle = await page.$(selector);
      if (!handle) continue;

      const visible = await handle.evaluate((el) => {
        const style = window.getComputedStyle(el);
        const rect = el.getBoundingClientRect();
        return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
      }).catch(() => false);

      if (visible) {
        return { selector, handle };
      }

      await handle.dispose();
    }

    await sleep(250);
  }

  return null;
}

async function habilitarDownloads(page, downloadPath) {
  const client = await page.target().createCDPSession();

  try {
    await client.send('Page.setDownloadBehavior', {
      behavior: 'allow',
      downloadPath,
    });
  } catch {
    await client.send('Browser.setDownloadBehavior', {
      behavior: 'allow',
      downloadPath,
    }).catch(() => {});
  }
}

async function esperarRedeAcalmar(page) {
  await page.waitForNetworkIdle({ idleTime: 1000, timeout: 15000 }).catch(() => {});
  await sleep(500);
}

async function aguardarNovaAba(browser, timeoutMs) {
  return new Promise((resolve) => {
    const timer = setTimeout(() => {
      browser.off('targetcreated', listener);
      resolve(null);
    }, timeoutMs);

    async function listener(target) {
      if (target.type() !== 'page') return;
      clearTimeout(timer);
      browser.off('targetcreated', listener);
      const novaPagina = await target.page().catch(() => null);
      resolve(novaPagina);
    }

    browser.on('targetcreated', listener);
  });
}

async function aguardarArquivoNovo(downloadDir, arquivosAntes, timeoutMs) {
  const deadline = Date.now() + timeoutMs;

  while (Date.now() < deadline) {
    const arquivosAgora = Array.from(listarArquivos(downloadDir));
    const novos = arquivosAgora.filter((arquivo) => !arquivosAntes.has(arquivo));
    const finalizados = novos.filter((arquivo) => !arquivo.endsWith('.crdownload') && !arquivo.endsWith('.tmp'));

    if (finalizados.length > 0) {
      const arquivo = finalizados[0];
      const tamanho1 = fs.statSync(arquivo).size;
      await sleep(800);
      const tamanho2 = fs.existsSync(arquivo) ? fs.statSync(arquivo).size : -1;

      if (tamanho1 === tamanho2 && tamanho2 > 0) {
        return arquivo;
      }
    }

    await sleep(500);
  }

  return null;
}

function montarCaminhoArquivo(candidato, paginaAtual, indiceNaPagina, urlOuArquivo = '') {
  const nomeOriginal = extrairNomeDoUrlOuArquivo(urlOuArquivo);
  const basePorLinha = extrairIdentificador(candidato.linha || candidato.texto || candidato.titulo || '');
  const base = basePorLinha || nomeOriginal || `acordao_p${paginaAtual}_${indiceNaPagina}`;
  const hash = hashCurto(String(urlOuArquivo || `${base}_${paginaAtual}_${indiceNaPagina}`));
  const nome = limparNomeArquivo(`${base}_${hash}.pdf`);
  return path.join(CONFIG.pastaDownloads, nome);
}

function extrairIdentificador(texto) {
  const normalizado = (texto || '').replace(/\s+/g, ' ').trim();
  const acordao = normalizado.match(/ac[oó]rd[ãa]o\s*(?:n[º°.]?|no\.?)?\s*[\w./-]+/i);
  if (acordao) return acordao[0];

  const processo = normalizado.match(/processo\s*(?:n[º°.]?|no\.?)?\s*[\w./-]+/i);
  if (processo) return processo[0];

  return normalizado.slice(0, 80);
}

function extrairNomeDoUrlOuArquivo(valor) {
  if (!valor) return '';
  try {
    const url = new URL(valor);
    return path.basename(url.pathname, path.extname(url.pathname));
  } catch {
    return path.basename(valor, path.extname(valor));
  }
}

function limparNomeArquivo(nome) {
  return nome
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .replace(/[<>:"/\\|?*\x00-\x1F]/g, '_')
    .replace(/\s+/g, '_')
    .replace(/_+/g, '_')
    .slice(0, 180);
}

function caminhoUnico(caminho) {
  const ext = path.extname(caminho);
  const dir = path.dirname(caminho);
  const stem = path.basename(caminho, ext);
  let atual = caminho;
  let contador = 1;

  while (fs.existsSync(atual)) {
    atual = path.join(dir, `${stem}_${contador}${ext}`);
    contador += 1;
  }

  return atual;
}

function hashCurto(value) {
  return crypto.createHash('sha1').update(value).digest('hex').slice(0, 10);
}

function listarArquivos(dir) {
  if (!fs.existsSync(dir)) return new Set();
  return new Set(
    fs.readdirSync(dir)
      .map((nome) => path.join(dir, nome))
      .filter((arquivo) => fs.statSync(arquivo).isFile()),
  );
}

async function assinaturaResultados(page) {
  return page.evaluate(() => document.body.innerText.slice(0, 4000)).catch(() => '');
}

function garantirDiretorio(dir) {
  fs.mkdirSync(dir, { recursive: true });
}

function formatarDataBR(data) {
  const dia = String(data.getDate()).padStart(2, '0');
  const mes = String(data.getMonth() + 1).padStart(2, '0');
  const ano = data.getFullYear();
  return `${dia}/${mes}/${ano}`;
}

function formatarDataApi(data) {
  const dia = String(data.getDate()).padStart(2, '0');
  const mes = String(data.getMonth() + 1).padStart(2, '0');
  const ano = data.getFullYear();
  return `${ano}${mes}${dia}`;
}

function normalizarDataApi(dataBR) {
  if (/^\d{8}$/.test(dataBR)) {
    return dataBR;
  }
  const [dia, mes, ano] = dataBR.split('/');
  return `${ano}${mes}${dia}`;
}

function parseDataApi(dataApi) {
  const data = String(dataApi || '');
  if (!/^\d{8}$/.test(data)) {
    throw new Error(`Data API inválida: ${dataApi}`);
  }

  return new Date(
    Number(data.slice(0, 4)),
    Number(data.slice(4, 6)) - 1,
    Number(data.slice(6, 8)),
  );
}

function gerarJanelasMensais(inicioApi, fimApi) {
  const inicio = parseDataApi(inicioApi);
  const fim = parseDataApi(fimApi);
  const janelas = [];

  let cursor = new Date(inicio.getFullYear(), inicio.getMonth(), 1);

  while (cursor <= fim) {
    const inicioJanela = cursor.getFullYear() === inicio.getFullYear()
      && cursor.getMonth() === inicio.getMonth()
      ? inicio
      : new Date(cursor.getFullYear(), cursor.getMonth(), 1);

    const fimDoMes = new Date(cursor.getFullYear(), cursor.getMonth() + 1, 0);
    const fimJanela = fimDoMes > fim ? fim : fimDoMes;

    const inicioFormatado = formatarDataApi(inicioJanela);
    const fimFormatado = formatarDataApi(fimJanela);
    janelas.push({
      inicioApi: inicioFormatado,
      fimApi: fimFormatado,
      label: `${inicioFormatado}_${fimFormatado}`,
    });

    cursor = new Date(cursor.getFullYear(), cursor.getMonth() + 1, 1);
  }

  return janelas;
}

function montarCsv(registros) {
  const colunas = [
    'dataSessao',
    'numeroProcesso',
    'anoProcesso',
    'numeroDecisorio',
    'anoDecisorio',
    'natureza',
    'orgao',
    'partes',
    'objeto',
    'interessados',
    'relatorVotoDecisao',
    'tipoDecisorio',
    'janelaBusca',
    'caminhoArquivo',
    'pdfLocal',
  ];

  const linhas = registros.map((registro) => (
    colunas.map((coluna) => csvEscape(registro[coluna])).join(',')
  ));

  return `\ufeff${colunas.join(',')}\n${linhas.join('\n')}\n`;
}

function csvEscape(valor) {
  if (valor === null || valor === undefined) {
    return '';
  }

  const texto = String(valor);
  if (!/[",\r\n;]/.test(texto)) {
    return texto;
  }

  return `"${texto.replace(/"/g, '""')}"`;
}

async function fetchComTimeout(url, options = {}, timeoutMs = 45000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);

  try {
    return await fetch(url, {
      ...options,
      signal: controller.signal,
    });
  } finally {
    clearTimeout(timer);
  }
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function delayAleatorio() {
  const ms = CONFIG.delays.min + Math.floor(Math.random() * (CONFIG.delays.max - CONFIG.delays.min + 1));
  await sleep(ms);
}

main().catch((error) => {
  console.error(`[FATAL] ${error.stack || error.message}`);
  process.exitCode = 1;
});
