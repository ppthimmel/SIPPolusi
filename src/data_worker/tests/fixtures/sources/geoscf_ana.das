Attributes {
    NC_GLOBAL {
        String title "GEOS-CF (Composition Forecast)";
        String Conventions "COARDS", "GrADS";
        String dataType "Grid";
        String history "Thu Nov 26 16:06:58 EST 2026 : imported by GrADS Data Server 2.0";
        String extra_das_attribute "This is an example of metadata added using a supplementary DAS file. See testdata/extra.das for the source.";
    }
    lon {
        String grads_dim "x";
        String grads_mapping "linear";
        String grads_size "1440";
        String units "degrees_east";
        String long_name "longitude";
        Float64 minimum -180.00000000000;
        Float64 maximum 179.75000000000;
        Float32 resolution 0.25;
    }
    lat {
        String grads_dim "y";
        String grads_mapping "linear";
        String grads_size "721";
        String units "degrees_north";
        String long_name "latitude";
        Float64 minimum -90.00000000000;
        Float64 maximum 90.00000000000;
        Float32 resolution 0.25;
    }
    time {
        String grads_dim "t";
        String grads_mapping "linear";
        String grads_size "11496";
        String grads_min "09:30z04aug2025";
        String grads_step "60mn";
        String units "days since 1-1-1 00:00:0.0";
        String long_name "time";
        String minimum "09:30z04aug2025";
        String maximum "08:30z26nov2026";
        Float32 resolution 63.431053;
    }
    lev {
        String grads_dim "z";
        String grads_mapping "linear";
        String units "millibar";
        String long_name "altitude";
        Float64 minimum 72.00000000000;
        Float64 maximum 72.00000000000;
    }
    co {
        Float32 _FillValue 1.0E15;
        Float32 missing_value 1.0E15;
        String long_name "carbon monoxide (co, mw=28.01 g mol-1) mole fraction in dry air (mol mol-1) ";
    }
    no2 {
        Float32 _FillValue 1.0E15;
        Float32 missing_value 1.0E15;
        String long_name "nitrogen dioxide (no2, mw=46.01 g mol-1) mole fraction in dry air (mol mol-1) ";
    }
    o3 {
        Float32 _FillValue 1.0E15;
        Float32 missing_value 1.0E15;
        String long_name "ozone (o3, mw=48.0 g mol-1) mole fraction in dry air (mol mol-1) ";
    }
    pm10_rh35 {
        Float32 _FillValue 1.0E15;
        Float32 missing_value 1.0E15;
        String long_name "particulate matter with diameter less than 10 um mass at rh 35% (includes water) mass concentration in dry air (ug m-3) ";
    }
    pm25_rh35 {
        Float32 _FillValue 1.0E15;
        Float32 missing_value 1.0E15;
        String long_name "particulate matter with diameter less than 2.5 um mass at rh 35% (includes water) mass concentration in dry air (ug m-3) ";
    }
    so2 {
        Float32 _FillValue 1.0E15;
        Float32 missing_value 1.0E15;
        String long_name "sulfur dioxide (so2, mw=64.04 g mol-1) mole fraction in dry air (mol mol-1) ";
    }
}
