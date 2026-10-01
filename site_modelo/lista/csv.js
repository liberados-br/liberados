// Baixar a lista em CSV.
import { C, D, E, N, NAO_VERIFICADO, SS, VF, estado } from './estado.js';

// --------------------------------------------------------------------- csv

function baixarCsv() {
  const linhas = ['dominio,situacao,candidatos,relevancia,elegivel_leilao,verificado_em'];
  for (const it of estado.atual) {
    const quando = it[VF] ? new Date(it[VF] * 1000).toISOString() : '';
    const situacao = it[SS] === NAO_VERIFICADO ? 'NAO_VERIFICADO' : estado.dados.status[it[SS]];
    linhas.push([it[D], situacao, it[C] ?? '', it[N],
                 it[E] ? 'sim' : 'nao', quando].join(','));
  }
  const blob = new Blob([linhas.join('\n')], { type: 'text/csv;charset=utf-8' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `liberado-${estado.filtro}.csv`;
  a.click();
  URL.revokeObjectURL(a.href);
}

export {
  baixarCsv,
};
