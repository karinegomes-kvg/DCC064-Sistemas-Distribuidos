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
docker-compose up --build 
```

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
Ao encerrar o serviço, as consultas já aceitas são finalizadas antes de fechar
o socket. Para carregar as alterações, execute `docker compose restart`.

