#!/bin/bash
# Registra o runner self-hosted do GitHub Actions nesta instância EC2 (Lab 04.2).
#
# CAMINHO MANUAL (este script). Existe também um CAMINHO AUTOMÁTICO (decisão
# D44, padrão): `make runner-token` grava o token como SecureString no SSM
# Parameter Store ANTES de `make runner-apply`; o user-data lê, registra e
# apaga o parâmetro sozinho no boot — sem sessão SSM interativa, sem login do
# GitHub no Codespaces (ver terraform/runner/templates/user_data.sh.tftpl,
# seção 6.1). Use este script manual só se o automático não rodou (parâmetro
# nunca foi gravado) ou falhou (ver /var/log/user-data.log na instância).
#
# Roda manualmente, dentro de uma sessão SSM Session Manager, iniciada pelo
# aluno depois que o user-data terminar (terraform/runner/templates/
# user_data.sh.tftpl). O pipeline de CI/CD NUNCA chama este script: o registro
# exige colar um token do GitHub de curta duração (poucos minutos), o que é
# por natureza uma ação humana — ver 01_RESEARCH_FINDINGS_AND_DECISIONS.md §9.
#
# Modelo de segurança completo em spec-register-runner.md (agente A4); este
# script implementa a especificação de lá. Resumo do que importa:
#   - o token nunca é argumento de linha de comando deste script (nunca
#     "./register_runner.sh SEU_TOKEN"): é lido interativamente com `read -rs`,
#     sem eco no terminal e sem entrar em .bash_history/.zsh_history;
#   - nenhum `set -x` neste arquivo, em nenhum momento — rastrear a execução
#     exporia o token nos logs;
#   - risco residual aceito e documentado (ver bloco antes do config.sh): a
#     ferramenta oficial do GitHub só aceita o token como flag de linha de
#     comando, então ele fica visível a quem ler /proc nesta instância durante
#     a janela curta da chamada. Instância é de uso único e dedicada — sem
#     outro usuário/processo não confiável rodando ao mesmo tempo.

set +o xtrace
set -o errexit -o nounset -o pipefail
unset HISTFILE
set +o history 2>/dev/null || true

if [ "$#" -ne 0 ]; then
  echo "Erro: este script não aceita argumentos. Nunca passe o token na linha de comando" >&2
  echo "(ele ficaria em ~/.*_history e em 'ps aux' do processo deste script)." >&2
  echo "Rode sem argumentos: o script pede o token de forma interativa e oculta." >&2
  exit 1
fi

RUNNER_DIR=/opt/actions-runner
RUNNER_USER=actions-runner
REPO_URL="https://github.com/vamperst/FIAP-Cloud-Based-Machine-Learning"
RUNNER_LABELS="self-hosted,linux,x64,academy-slm-deploy"

if [ ! -f "$RUNNER_DIR/.provisionado" ]; then
  echo "Erro: $RUNNER_DIR/.provisionado não existe — o user-data ainda não terminou" >&2
  echo "(ou falhou). Confira /var/log/user-data.log antes de continuar." >&2
  exit 1
fi

if ! id -u "$RUNNER_USER" >/dev/null 2>&1; then
  echo "Erro: usuário de sistema '$RUNNER_USER' não existe. O user-data deveria tê-lo" >&2
  echo "criado — confira /var/log/user-data.log." >&2
  exit 1
fi

# Nome determinístico via instance-id (IMDSv2, token obrigatório — mesma regra
# dura de todo o lab): se a instância for destruída e recriada, o próximo
# registro usa um nome diferente automaticamente, sem colisão nem precisar de
# "--replace" para limpar lixo de execuções antigas de outra instância.
imds_token=$(curl -fsS -X PUT "http://169.254.169.254/latest/api/token" \
  -H "X-aws-ec2-metadata-token-ttl-seconds: 60")
instance_id=$(curl -fsS -H "X-aws-ec2-metadata-token: $imds_token" \
  "http://169.254.169.254/latest/meta-data/instance-id")
runner_name="academy-slm-deploy-${instance_id}"

echo "===============================================================" >&2
echo "Cole abaixo o token de registro do GitHub:" >&2
echo "  Settings -> Actions -> Runners -> New self-hosted runner" >&2
echo "  ($REPO_URL/settings/actions/runners/new)" >&2
echo "O token some da tela ao digitar/colar e não fica em histórico nenhum." >&2
echo "Ele expira em poucos minutos — copie e cole aqui na sequência." >&2
echo "===============================================================" >&2
read -rs registration_token
echo >&2

if [ -z "${registration_token:-}" ]; then
  echo "Erro: token vazio, abortando sem tentar registrar." >&2
  exit 1
fi

echo "Registrando runner '$runner_name' com labels [$RUNNER_LABELS] (ephemeral: um job e sai)." >&2
cd "$RUNNER_DIR"

# RISCO RESIDUAL DOCUMENTADO (spec-register-runner.md §4): config.sh só aceita
# o token via --token, nunca por variável de ambiente ou stdin. Da linha
# abaixo até o `unset` seguinte é a única janela em que o token existe fora da
# variável lida por `read`; nenhum comando depois deste bloco depende dela.
sudo --preserve-env=RUNNER_ALLOW_RUNASROOT -u "$RUNNER_USER" ./config.sh \
  --unattended \
  --replace \
  --ephemeral \
  --url "$REPO_URL" \
  --token "$registration_token" \
  --name "$runner_name" \
  --labels "$RUNNER_LABELS"
unset registration_token

echo "Registro concluído. Instalando o serviço systemd e iniciando (aguarda exatamente um job)." >&2
sudo ./svc.sh install "$RUNNER_USER"
sudo ./svc.sh start

echo "===============================================================" >&2
echo "Runner '$runner_name' registrado e rodando como serviço." >&2
echo "Confirme em $REPO_URL/settings/actions/runners — deve aparecer 'Idle'." >&2
echo "Depois de processar um job (--ephemeral), ele se desregistra automaticamente" >&2
echo "e o serviço para; rode este script de novo para registrar a próxima execução." >&2
echo "===============================================================" >&2
