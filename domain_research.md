# Domain Research — Surface AQI & HCHO Hotspot Detection (GGSIPU2603)
**Owner:** Team C, Member 2 (Pitch, Docs & Domain Research)
**Last updated:** Day 1

---

## 1. HCHO (Formaldehyde) Health Effects

- WHO's indoor air quality guideline sets formaldehyde at **0.1 mg/m³ (0.08 ppm)** as a 30-minute average, meant to prevent sensory irritation of eyes, nose, and throat.
- IARC classifies formaldehyde as a **Group 1 human carcinogen**.
- Long-term exposure is associated with central nervous system effects: headaches, mood changes, impaired memory and coordination.
- HCHO is not just directly harmful — it's a **marker gas** for biomass burning and industrial VOC emissions, and a **precursor to ground-level ozone**.

**Sources:**
- WHO Guidelines for Indoor Air Quality: Selected Pollutants — https://www.ncbi.nlm.nih.gov/books/NBK138711/
- ATSDR Medical Management Guidelines — https://wwwn.cdc.gov/TSP/MMG/MMGDetails.aspx?mmgid=216&toxid=39

---

## 2. Stubble Burning — Punjab & Haryana

- From mid-Sept to mid-Nov (most recent season), Punjab recorded **9,655 stubble-burning incidents**, a ~71% drop from **33,719** in the same period the prior year.
- **Key satellite-monitoring gap:** official tracking relies on MODIS/VIIRS satellites that observe the ground only between **10:30am–1:30pm**. Farmers have shifted burning to late afternoon/evening — over **90% of large Punjab farm fires in 2024–2025 occurred after 3pm**, up from just 3% in 2021.
- This is a strong talking point: even official satellite fire-tracking has documented blind spots. A multi-source fusion approach (like ours) reduces reliance on any single satellite's overpass timing.

**Sources:**
- Tribune India, "Satellites miss majority of stubble fires" — https://www.tribuneindia.com/news/delhi/satellites-miss-majority-of-stubble-fires-delhi-air-pollution-underestimated-report
- Deccan Herald farm-fire data — https://www.deccanherald.com

---

## 3. CPCB Ground Monitoring Network — Coverage Gaps

- CPCB's manual monitoring network (NAMP) had **966 operating stations** across 419 cities/towns in 28 states + 7 UTs (as of late 2024).
- Only **12% of India's census towns and cities** have any air quality monitoring station at all (CSE analysis).
- Even where stations exist, uptime isn't guaranteed — Haryana's state government admitted only **30 of 72** official monitoring stations were operational.
- **Implication for our project:** rural/agricultural regions — including the Punjab stubble belt — have little to no ground-level AQI visibility. Satellite-based surface AQI estimation directly targets this gap.

**Sources:**
- CPCB NAMP — https://cpcb.nic.in/about-namp/
- CSE, "Only 12% of India's census cities and towns have air quality monitoring stations" — https://www.cseindia.org/only-12-per-cent-of-india-s-census-cities-and-towns-have-air-quality-monitoring-stations-11779

---

## 4. Problem Statement Slide (Draft)

**Slide title:** Surface AQI & HCHO hotspots, seen from space

**The gap:** India's ground monitoring network covers a small fraction of the country, concentrated in major cities. Most of rural and semi-urban India — including regions that drive seasonal pollution, like the Punjab-Haryana stubble belt — has no way to know its own air quality in real time.

**What we're building:** A system that fuses free satellite data (Sentinel-5P TROPOMI) with the ground stations that do exist, to estimate surface AQI everywhere — not just where sensors are — and flag HCHO hotspots that point to specific pollution sources.

**Why it matters:** Even official fire-tracking satellites have documented blind spots (timing-of-overpass gaps). A software layer that fuses multiple signals and fills monitoring gaps gives citizens, researchers, and regulators a fuller picture than any single data source alone.

---

## 5. Likely Judge Questions — Draft Answers

*(Refine these once Team A/B have real numbers — swap in actual RMSE, actual hotspot counts, actual sample sizes.)*

1. **"How do you validate satellite-estimated AQI against ground truth?"**
   Train/test split by station or time period (not random), report RMSE/R² against CPCB station values. Be upfront that accuracy varies by region — better near training stations, more uncertain in fully ungauged areas.

2. **"Why not just use an existing AQI app (SAFAR, IQAir)?"**
   Those apps interpolate *between* existing ground stations; they don't add new observational coverage where there are zero stations. Satellite data is the actual new information source.

3. **"What's your HCHO hotspot detection threshold based on?"**
   Percentile-based (top 5–10% of column density) or statistical anomaly detection, cross-validated against FIRMS fire-activity data so the threshold isn't arbitrary.

4. **"How do you handle cloud cover / missing satellite data?"**
   QA-flag filtering plus temporal compositing (weekly/monthly means) to smooth over cloudy-day gaps.

5. **"Satellites measure the whole atmospheric column, not the surface — how do you correct for that?"**
   Acknowledge this is the hardest technical part. Explain the correction approach (boundary layer height, meteorology) and its limitations rather than overclaiming precision.

6. **"What's the real-world deployment path for this?"**
   Complement to CPCB infrastructure, not a replacement — early-warning in ungauged regions, and pinpointing pollution sources to support enforcement efforts (e.g. stubble-burning enforcement squads).

7. **"How is this different from ISRO/CAQM's existing satellite fire protocol?"**
   Their focus is fire detection for enforcement; ours is continuous AQI + HCHO source-hotspot mapping as a broader public-facing tool — complementary, not duplicate.

---

## 6. Architecture (Data Flow)

```
Satellite data (Sentinel-5P: HCHO, NO2, AOD)   Ground AQI data (CPCB / OpenAQ)
                    \                                  /
                     \                                /
                      v                              v
                 Fusion & preprocessing
              (cloud filter, regrid, join met data)
                            |
                            v
                        Modeling
           (AQI regression + hotspot clustering)
                            |
                            v
                        Dashboard
                (interactive map, time slider)
```
