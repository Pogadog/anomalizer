import pandas as pd
import re

df1 = pd.DataFrame([[1,2,3,4],[2,3,4,5],[3,4,5,6]]).T
df2 = pd.DataFrame([[10,20,30,40],[20,30,40,50],[30,40,50,60]]).T

df1.columns=['job=1,host=a,foo=1', 'job=1,host=b,foo=2', 'job=2,host=c,foo=3']
df2.columns=['job=1,host=a', 'job=1,host=b', 'job=2,host=c']

print(df1)
print(df2)

by = '(job,foo)'
groups = re.split('[(,)]', by)[1:-1]

# aggregate on job and host, eliminating foo etc.
groupby='(' + '|'.join(groups) + ').*'

tags = [t.split(',') for t in df1.columns]
gtags = [[t for t in tt if re.match(groupby, t)] for tt in tags]
gtags = [','.join(gt) for gt in gtags]
print(tags)
print(gtags)
df1t = df1.copy(deep=False)
df1t.columns = gtags

df1t = df1t.groupby(lambda x:x, axis=1).sum()
print(df1t)


