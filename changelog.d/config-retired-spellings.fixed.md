`config migrate` keeps the value runs read when a setting has both a retired
old spelling and one still read; it no longer keeps the retired one's. A write
that checks a setting's current value no longer sees a retired spelling's.
