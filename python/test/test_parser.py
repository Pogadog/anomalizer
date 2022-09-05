# parser grammar for a tiny language that can compute expressions of functions e.g. sum(a+b)/max(c)*d

from typing import List, Tuple, Union
from funcparserlib.lexer import make_tokenizer, TokenSpec, Token
from funcparserlib.parser import tok, Parser, many, maybe, forward_decl, finished
from funcparserlib.util import pretty_tree
from dataclasses import dataclass
import re

import pandas as pd
import numpy as np

@dataclass
class BinaryExpr:
    op: str
    left: "Expr"
    right: "Expr"

@dataclass
class FunctionalExpr:
    op: str
    args: "ArgsExpr"
    rate: "Rate"

@dataclass
class ArgsExpr:
    args: "Args"

@dataclass
class TaggedExpr:
    args: "tuple"

Expr = Union[BinaryExpr, FunctionalExpr, int, float]

def tokenize(s: str) -> List[Token]:
    specs = [
        TokenSpec("whitespace", r"\s+"),
        TokenSpec("float", r"\d+\.\d*([Ee][+\-]?\d+)*"),
        TokenSpec("int", r"\d+"),
        TokenSpec("op", r"(\*\*)|[+\-*/()]"),
    ]
    tokenizer = make_tokenizer(specs)
    return [t for t in tokenizer(s) if t.type != "whitespace"]


def op(name: str) -> Parser[Token, str]:
    return tok("op", name)

def to_expr(args: Tuple[Expr, List[Tuple[str, Expr]]]) -> Expr:
    first, rest = args
    result = first
    for op, expr in rest:
        result = BinaryExpr(op, result, expr)
    return result

def to_tags(tags):
    return tags

MUL = {'s': 1, 'm': 60, 'h': 60*60}

def to_func(args):
    if args[2]:
        rate = int(args[2][1:-2])
        rate *= MUL[args[2][-2]]
    else:
        rate = 0
    return FunctionalExpr(args[0], args[1], rate)

def to_tagged(args):
    if args[1]:
        return TaggedExpr([args[0]] + [[(a[0], a[1], a[2]) for a in args[1]]])
    else:
        return args[0]

def to_args(args):
    result = ArgsExpr([args[0]] +  [a[0] for a in args[1:][0]])
    return result

def parse(tokens: List[Token]) -> Expr:
    int_num = tok("int") >> int
    float_num = tok("float") >> float
    number = int_num | float_num

    expr: Parser[Token, Expr] = forward_decl()
    parenthesized = -op("(") + expr + -op(")")
    primary = number | parenthesized
    mul = primary + many((op("*") | op("/")) + primary) >> to_expr
    sum = mul + many((op("+") | op("-")) + mul) >> to_expr
    expr.define(sum)

    document = expr + -finished

    return document.parse(tokens)


if __name__=='__main__':

    print(tokenize('1+2'))
    print(parse(tokenize('1*2+3')))
    print(parse(tokenize('(1+2)/(3+4)')))


def tokenize(s: str) -> List[Token]:
    specs = [
        TokenSpec("rate", r'\[\d+(s|m|h)\]'),
        TokenSpec('string', r'"[^\"]*"'),
        TokenSpec("float", r"\d+\.\d*([Ee][+\-]?\d+)*"),
        TokenSpec("int", r"\d+"),
        TokenSpec('whitespace', r'\s+'), TokenSpec('string', r'"[^\"]*"'),
        TokenSpec('name', r'[\w]+'), TokenSpec('op', r'(=~)|[(){}=,+\-*/]'),
    ]
    tokenizer = make_tokenizer(specs)
    return [t for t in tokenizer(s) if t.type != 'whitespace']

def op(name: str) -> Parser[Token, str]:
    return tok("op", name)

def pretty_expr(expr: Expr) -> str:
     def kids(expr: Expr) -> List[Expr]:
         if isinstance(expr, BinaryExpr):
             return [expr.left, expr.right]
         else:
             return []
     def show(expr: Expr) -> str:
         if isinstance(expr, BinaryExpr):
             return f"BinaryExpr({expr.op!r})"
         else:
             return repr(expr)

     return pretty_tree(expr, kids, show)

def _max(x):
    if isinstance(x, pd.DataFrame) and x.shape[1]>1:
        return x.max(axis=1)
    else:
        return float(x.max())

OPERATORS = {'+': pd.DataFrame.__add__, '-': pd.DataFrame.__sub__, '*': pd.DataFrame.__mul__, '/': pd.DataFrame.__truediv__}
FUNCTIONS = {'sum': lambda x,r: pd.DataFrame.sum(x, axis=1), 'max': lambda x,r: _max(x),
             'rate': lambda x,r: x.diff().fillna(0).rolling(max(1, r//60)).mean().fillna(0), None: lambda x,r: x}

def eval_tree(data: dict, expr: Expr):
    import numbers
    if isinstance(expr, numbers.Number):
        return expr
    if isinstance(expr, BinaryExpr):
        return OPERATORS[expr.op](eval_tree(data, expr.left), eval_tree(data, expr.right))
    elif isinstance(expr, FunctionalExpr):
        args = eval_tree(data, expr.args)
        return FUNCTIONS[expr.op](args, expr.rate)
    elif isinstance(expr, ArgsExpr):
        result = [eval_tree(data, e) for e in expr.args]
        df = pd.concat(result, axis=1)
        df.columns = expr.args
        return df
    elif isinstance(expr, TaggedExpr):
        df = eval_tree(data, expr.args[0])
        filter = expr.args[1]
        # set up exact and regex matches.
        exacts = {}
        regexs = {}
        for f in filter:
            if f[1]=='=':
                exacts[f[0]] = f[2].replace('"', '')
            else:
                regexs[f[0]] = f[2].replace('"', '')
        result = pd.DataFrame();
        # match exact and regex terms for each column.
        for col,values in df.iteritems():
            tags = col.split(',')
            tags = dict([(t.split('=')[0].strip(), t.split('=')[1].replace('"', '').strip()) for t in tags])
            found = True
            for e in exacts:
                if tags.get(e)!=exacts[e]:
                    found = False
                    break
            if found:
                for r in regexs:
                    if not re.match(tags.get(r, '$'), regexs[r]):
                        found = False
                        break
            if found:
                result[col] = values
        return result
    return data[expr]

expr = forward_decl()
args = expr + -op(',') + many(expr + maybe(-op(','))) >> to_args
func = maybe(tok('name')) + -op('(') + (args | expr) + maybe(tok('rate')) + -op(')') >> to_func
paren = -op('(') + expr + -op(')')
int_num = tok("int") >> int
float_num = tok("float") >> float
number = int_num | float_num

tags = -op('{') + many(tok('name') + (op('=~') | op('=')) + tok('string') + maybe(-op(','))) + -op('}') >> to_tags
subexpr = (func | paren | tok('name')) + maybe(tags) >> to_tagged | number
mul = subexpr + many((op('*') | op('/')) + subexpr) >> to_expr
sum = mul + many((op('+') | op('-')) + mul) >> to_expr
expr.define(sum)
document = expr + -finished

if __name__=='__main__':
    DATAFRAMES = {}
    DATAFRAMES['node_memory_MemTotal_bytes'] = pd.DataFrame(np.random.uniform(0, 1, size=(20, 2))+1)
    DATAFRAMES['node_memory_MemAvailable_bytes'] = pd.DataFrame(np.random.uniform(0, 1, size=(20,2)))
    DATAFRAMES['anomalizer_active_threads'] = pd.DataFrame(np.random.uniform(0, 1, size=(20,1)), columns=['job="anomalizer-engine"'])
    DATAFRAMES['a'] = pd.DataFrame([1,2,3], columns=['job="1"'])
    DATAFRAMES['b'] = pd.DataFrame([4,5,6], columns=['job="1"'])
    DATAFRAMES['c'] = pd.DataFrame([7,8,9], columns=['job="1"'])
    DATAFRAMES['d'] = pd.DataFrame([10,11,12], columns=['job="1"'])
    DATAFRAMES['e'] = pd.DataFrame([[1,3],[2,2],[3,1]], columns=['job="1", target="2"', 'job="2"'])

    print(tokenize('rate(anomalizer_active_threads[5m])'))
    print(document.parse(tokenize('rate(anomalizer_active_threads[5m])')))
    print(eval_tree(DATAFRAMES, document.parse(tokenize('rate(anomalizer_active_threads[5m])'))))

    print(document.parse(tokenize('rate(anomalizer_active_threads{job="anomalizer-engine"}[5m])')))
    print(eval_tree(DATAFRAMES, document.parse(tokenize('rate(anomalizer_active_threads{job="anomalizer-engine"}[5m])'))))

    print(document.parse(tokenize('e{job="1"}')))
    print(eval_tree(DATAFRAMES, document.parse(tokenize('e{job="1"}'))))

    print(document.parse(tokenize('e{job="1", target="2"}')))
    print(eval_tree(DATAFRAMES, document.parse(tokenize('e{job="1",target="2"}'))))

    print(document.parse(tokenize('e{job="1", target=~"2"}')))
    print(eval_tree(DATAFRAMES, document.parse(tokenize('e{job="1",target=~"2"}'))))

    print(document.parse(tokenize('(a+b+e){job="1"}')))
    print(eval_tree(DATAFRAMES, document.parse(tokenize('(a+b+e){job="1"}'))))

    print(document.parse(tokenize('a+b')))
    print(document.parse(tokenize('a+b-c')))
    print(document.parse(tokenize('a*b')))
    print(document.parse(tokenize('a*b*c')))
    print(document.parse(tokenize('a*b+c*d')))
    print(document.parse(tokenize('a*(b+c)*d')))
    print(tokenize('max(a)'))
    print(document.parse(tokenize('max(a)')))
    print(eval_tree(DATAFRAMES, document.parse(tokenize('max(a)'))))
    print(tokenize('max(a,b,c)'))
    print(document.parse(tokenize('max(a,b,c)')))
    print(eval_tree(DATAFRAMES, document.parse(tokenize('max(a,b,c)'))))

    #print(document.parse(tokenize('a{job="1"}')))
    #print(eval_tree(DATAFRAMES, document.parse(tokenize('a{job="1"}'))))

    print(document.parse(tokenize('a/max(a)')))
    print(eval_tree(DATAFRAMES, document.parse(tokenize('a/max(a)'))))

    print(document.parse(tokenize('(a,b,c)/max(a)')))
    print(eval_tree(DATAFRAMES, document.parse(tokenize('(a,b,c)/max(a)'))))

    print(document.parse(tokenize('rate(a[2s])')))
    print(eval_tree(DATAFRAMES, document.parse(tokenize('rate(a[2s])'))))

    print(document.parse(tokenize('max(max(a,b,c))')))
    print(eval_tree(DATAFRAMES, document.parse(tokenize('max(max(a,b,c))'))))

    print(document.parse(tokenize('(a,b,c)/max(max(a,b,c))')))
    print(eval_tree(DATAFRAMES, document.parse(tokenize('(a,b,c)/max(max(a,b,c))'))))

    print(tokenize('(node_memory_MemTotal_bytes+node_memory_MemAvailable_bytes)*10'))
    parsed = document.parse(tokenize('(node_memory_MemTotal_bytes+node_memory_MemAvailable_bytes)/0.1'))
    print(pretty_expr(parsed))
    print(eval_tree(DATAFRAMES, parsed))

    print(tokenize('sum(node_memory_MemTotal_bytes - node_memory_MemAvailable_bytes) / sum(node_memory_MemTotal_bytes)*100'))
    parsed = document.parse(tokenize('sum(node_memory_MemTotal_bytes - node_memory_MemAvailable_bytes)/sum(node_memory_MemTotal_bytes)*100'))
    print(parsed)
    print(pretty_expr(parsed))
    print(eval_tree(DATAFRAMES, parsed))

    print(document.parse(tokenize('rate(node_memory_MemTotal_bytes[5m])')))
    print(eval_tree(DATAFRAMES, document.parse(tokenize('rate(node_memory_MemTotal_bytes[5m])'))))
