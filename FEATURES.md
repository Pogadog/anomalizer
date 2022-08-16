# Features emitted by the anomalizer engine

This is how things stand currently -- these need to be cleaned up but not at
this time.  

```
features {
  cluster: <int -- classified cluster of a metric>,
  clusters: [<list of clusters that the metric belongs in, more than one cluster implies an anomaly>]
  noisy: {
     snr: <float -- signal-to-noise ratio for a noisy metric>
  },
  normalized_features: <float -- the normalized features used to classify metric into a cluster.
                        used as the default sort order in a UI>,
  increasing: {
    increase: <float -- the rate of increase in a metric>
  },
  decreasing: {
    decrease: <float -- the rate of decrease in a metric (-ve)>
  },
  distribution {
    <tag> { 
      <gaussian|left-tailed|right-tailed>: <float -- percentage match with the distribution/100>      
    }  
}
```

## Clustering
Currently metrics are custered according to the following features, derived from the 
statitics associated with the metrics:

```
CLASSIFY_DATA.loc['rstd', idi] = stats.get('rstd', 0)
CLASSIFY_DATA.loc['mean_shift', idi] = stats.get('mean_shift', 0)
CLASSIFY_DATA.loc['spike', idi] = stats.get('spike', 0)
CLASSIFY_DATA.loc['dspike', idi] = stats.get('dspike', 0) # first-difference spike
```

These statistics are normalized so that they lie between 0..1, and are fed to the scikit.DBSCAN
algorithm for clustering analysis.  mean_shift is the driver for the increasing/decreasing features.

