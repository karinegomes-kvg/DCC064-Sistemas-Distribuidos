# Sistemas Distribuídos Trabalho 1

## Guia de Execução e Testes 

### Pré-requisitos
Antes de iniciar, garanta que a sua máquina possui os seguintes softwares instalados:
1. **Docker Engine / Docker Desktop**
2. **Docker Compose**
3. **Python 3.x** (para rodar o script cliente de teste no host)

---

### Passo 1: Subir os Contêineres do Mini-DNS

No terminal, navegue até a pasta do repositório e execute o comando abaixo para construir as imagens e subir a rede de servidores:

```bash
docker compose up -d --wait
```

O parâmetro `--wait` aguarda as portas UDP dos quatro serviços estarem abertas antes de liberar o terminal. A verificação lê `/proc/net/udp` dentro de cada contêiner Linux e não envia consultas nem consome cotas.

Este comando irá subir 4 contêineres na rede 172.20.0.0/16:
* root_dns (172.20.0.100:5300)
* tld_dns (172.20.0.101:5300)
* auth_dns (172.20.0.102:5300)
* local_resolver (172.20.0.50:5353, mapeado externamente para a porta UDP 5354)

### Passo 2: Executar o Teste
Abra um novo terminal e execute:

```bash
python client_test.py
```

### Consultas A e NS

O campo `qtype` seleciona o registro solicitado:

* `A`: endereço IP do nome exato, por exemplo `www.ufjf.br`.
* `NS`: servidor responsável pela zona exata, por exemplo `br` ou `ufjf.br`.
  Neste Mini-DNS, o campo `value` do registro NS contém o IP desse servidor.

Para executar consultas individuais:

```bash
python3 client_test.py www.ufjf.br --qtype A
python3 client_test.py br --qtype NS
python3 client_test.py ufjf.br --qtype NS
```

Sem opções adicionais, a busca é iterativa. Para usar resolução recursiva:

```bash
python3 client_test.py www.ufjf.br --recursiva
python3 client_test.py ufjf.br --qtype NS --recursiva
```

Nesse modo, o Resolver envia uma consulta com `rd=true` ao Root, que consulta os
próximos servidores e devolve a resposta final. Para executar a bateria de testes
inteira nesse modo, use `python3 client_test.py --recursiva`.
O cache é compartilhado entre os modos. Para observar uma busca recursiva sem
usar um registro já salvo, reinicie o Resolver com `docker compose restart local_resolver`.

Uma consulta NS para `ufjf.br` recebe o encaminhamento do Root para o TLD;
o TLD responde com `SUCCESS` e o registro NS em `answer`. Os encaminhamentos
intermediários continuam usando `REFERRAL` e `authority` para ambos os tipos.
Respostas de sucesso incluem `name`, `type`, `value` e `ttl` em cada registro.
O cache diferencia `(qname, qtype)` e informa o TTL restante nas respostas.

O protocolo simplificado usa `NXDOMAIN` quando não há registro do tipo pedido;
assim, consultar NS para `www.ufjf.br`, que só possui A na base atual, retorna
`NXDOMAIN`. Somente `A` e `NS` são aceitos; outros tipos também retornam
`NXDOMAIN`, sem consulta à rede nem gravação no cache.

Os valores dos registros são validados antes de responder, encaminhar ou salvar
no cache. Registros `A` precisam conter um IPv4 em texto, como `172.20.0.40`.
Neste Mini-DNS, `NS` também usa um IPv4 em `value`, conforme o formato simplificado
do trabalho. IPv6, nomes de servidores e endereços inválidos são recusados nesses
campos. O TTL, quando informado, precisa ser um inteiro maior ou igual a zero;
se estiver ausente, são usados 60 segundos para `A` e 300 para `NS`.

Cada consulta enviada pelo cliente, Resolver ou servidor recursivo recebe um
`transaction_id` aleatório entre 0 e 65535. Só são aceitas respostas com o mesmo
ID e vindas do IP e da porta consultados. O nome e o tipo da resposta também
são conferidos; encaminhamentos precisam indicar uma zona correspondente ao
domínio consultado. Pacotes incorretos são descartados sem reiniciar o timeout.
Ao responder ao cliente, o ID original dele é mantido, inclusive nas respostas
do cache e após encaminhamentos recursivos. Consultas com ID inválido são descartadas.

As consultas precisam informar `rd` como booleano JSON (`true` ou `false`) e
`qname` como texto não vazio. Valores como `"false"`, `0`, `null`, nome ausente ou
nome contendo apenas espaços são descartados antes de acessar o cache ou a rede.
O cliente faz essa validação antes de enviar; o Resolver e os servidores repetem
a validação ao receber a consulta.

Na entrada do Resolver, pacotes com JSON inválido, UTF-8 inválido ou JSON
profundamente aninhado são registrados e descartados. O processo continua
atendendo as próximas consultas, inclusive quando ocorre uma falha no envio
de uma resposta UDP.

### Prazo total das consultas

Cada consulta tem até 5 segundos no servidor, contados desde o recebimento do
pacote. A espera na fila e todos os encaminhamentos usam esse mesmo prazo.
Por exemplo, após 2 segundos na fila, restam 3 segundos para resolver e responder.
Na resolução iterativa, trocar de servidor não reinicia a contagem.

O campo opcional `tempo_restante` da consulta informa o orçamento em segundos.
Cada encaminhamento recursivo envia somente o tempo que ainda resta, e o nó
seguinte desconta a própria fila desse orçamento. O valor precisa ser um número
finito positivo; valores acima de 5 são limitados a 5. Consultas sem esse campo
recebem o prazo padrão. Cada nó usa um relógio monotônico local, sem depender da
sincronização dos relógios entre máquinas.

Consultas vencidas são descartadas antes de iniciar o processamento ou enviar a
resposta. Uma espera de rede que esgota o prazo não gera `NXDOMAIN`; o cliente
recebe timeout. O cliente espera até 6 segundos, reservando 1 segundo de margem
para transporte. Os valores ficam em `prazos.py` (`TEMPO_TOTAL` e `MARGEM_CLIENTE`).

Para carregar as alterações e testar os dois modos com o cache vazio:

```bash
docker compose up -d --force-recreate --wait
python3 client_test.py www.ufjf.br
python3 client_test.py ufjf.br --qtype NS --recursiva
```

### Atendimento de consultas simultâneas

O Resolver, Root, TLD e Autoritativo usam a rotina de `atendimento.py`. Cada
contêiner tem 4 trabalhadores e uma fila FIFO com até 8 consultas aguardando.
A thread principal recebe e valida os pacotes; os trabalhadores fazem a
resolução e enviam a resposta ao endereço de origem. Consultas inválidas são
descartadas antes de entrar na fila. Quando a fila está cheia, novas consultas
são descartadas sem bloquear o recebimento de outros pacotes.

Os limites ficam nas constantes `NUM_TRABALHADORES` e `LIMITE_FILA` de
`atendimento.py`. O cache do Resolver é protegido por um `Lock`, liberado antes
das consultas de rede. As consultas são retiradas da fila na ordem de chegada,
mas as respostas podem terminar em outra ordem; cada uma mantém o ID do cliente.
Ao encerrar o serviço, as consultas já aceitas são processadas ou descartadas
ao vencer o prazo, antes de fechar o socket. Para carregar as alterações,
execute `docker compose restart`.

### Limites de consultas por segundo

Antes de interpretar o JSON, cada contêiner aplica uma janela móvel de 1 segundo
com limites por IP e global. Trocar a porta de origem não renova a cota do IP.
Pacotes excedentes são descartados sem resposta, podendo causar timeout no cliente.
Pacotes malformados e consultas que depois encontram a fila cheia também consomem
a cota de entrada. Os avisos de excesso são limitados a um por segundo, com
contadores acumulados; ao encerrar, é registrado o total de descartes por limite.

Os valores iniciais do Compose são:

| Serviço | Por IP, por segundo | Global, por segundo |
| --- | ---: | ---: |
| Resolver | 20 | 100 |
| Root, TLD e Autoritativo | 100 | 200 |

O limite global vale para cada contêiner. Os servidores internos têm uma cota por
IP maior porque recebem consultas agregadas do Resolver ou de outro servidor DNS.
Esses valores são parâmetros iniciais de proteção, ainda não medidos como
capacidade de atendimento. Nos testes de escala, registre os limites usados para
distinguir descartes deliberados de saturação do servidor.

Para configurar o Resolver sem editar o código:

```bash
LIMITE_RESOLVER_POR_IP=40 LIMITE_RESOLVER_GLOBAL=150 docker compose up -d --wait
```

Para os servidores internos, as variáveis são `LIMITE_DNS_POR_IP` e
`LIMITE_DNS_GLOBAL`. Todos os valores precisam ser inteiros positivos.
As variáveis do Compose viram `LIMITE_POR_IP` e `LIMITE_GLOBAL` dentro de cada
contêiner. Execute `docker compose up -d --wait` para aplicar os novos valores; o Compose
recria os serviços cuja configuração mudou.

Os controles são acessados somente pela thread de recebimento. O histórico guarda
apenas admissões do último segundo e nunca supera a cota global; os IPs sem
admissões recentes são removidos ao receber novos pacotes.

### Verificar os limites no Docker

Os processos usam `python -u` para mostrar os logs imediatamente. Na pasta do
projeto, configure uma cota pequena para testar com quatro consultas:

```bash
LIMITE_RESOLVER_POR_IP=2 LIMITE_RESOLVER_GLOBAL=100 docker compose up -d --wait
python3 client_test.py www.ufjf.br
sleep 1.1
```

A primeira consulta deve retornar `SUCCESS` e aquecer o cache. A espera de 1,1
segundo libera a cota usada por ela. Em outro terminal, acompanhe os avisos:

```bash
docker compose logs -f --timestamps local_resolver
```

No primeiro terminal, inicie quatro clientes juntos:

```bash
for i in 1 2 3 4; do
    python3 client_test.py www.ufjf.br &
done
wait
```

Com o ambiente ocioso e as quatro consultas chegando dentro de um segundo,
espere duas respostas e dois timeouts, com um aviso de limite por IP no log.
Os avisos são agrupados; não há um aviso por pacote descartado. Após o grupo
terminar, uma consulta normal deve responder novamente.

Para isolar o limite global, configure uma cota por IP maior que a global e
repita o aquecimento, a espera e o grupo de clientes:

```bash
LIMITE_RESOLVER_POR_IP=10 LIMITE_RESOLVER_GLOBAL=2 docker compose up -d --wait
```

Nesse caso, o aviso deve registrar descartes globais. Para voltar aos valores
padrão do Resolver:

```bash
LIMITE_RESOLVER_POR_IP=20 LIMITE_RESOLVER_GLOBAL=100 docker compose up -d --wait
```

Para rodar os testes automáticos dos limites, sem depender dos contêineres:

```bash
python3 -B -m unittest discover -s tests -p 'test_controle_carga.py' -v
```

