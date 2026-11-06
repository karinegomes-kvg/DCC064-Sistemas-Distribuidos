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

