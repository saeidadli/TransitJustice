"""
Uses otp API to preform network analysis.
"""

import json
import time
from datetime import datetime

import requests

import pandas as pd
import geopandas as gpd
import shapely.geometry as geom

#=====================general functions==========================
#decode an encoded string
def decode(encoded):
    """
    An algorithms to decode the string to create a list of latitude,longitude coordinates.
    """
    #six degrees of precision in valhalla
    inv = 1.0 / 1e6;
    
    decoded = []
    previous = [0,0]
    i = 0
    #for each byte
    while i < len(encoded):
        #for each coord (lat, lon)
        ll = [0,0]
        for j in [0, 1]:
            shift = 0
            byte = 0x20
            #keep decoding bytes until you have this coord
            while byte >= 0x20:
                byte = ord(encoded[i]) - 63
                i += 1
                ll[j] |= (byte & 0x1f) << shift
                shift += 5
            #get the final value adding the previous offset and remember it for the next
            ll[j] = previous[j] + (~(ll[j] >> 1) if ll[j] & 1 else (ll[j] >> 1))
            previous[j] = ll[j]
        #scale by the precision and chop off long coords also flip the positions so
        #its the far more standard lon,lat instead of lat,lon
        decoded.append([float('%.6f' % (ll[1] * inv)), float('%.6f' % (ll[0] * inv))])
        #hand back the list of coordinates
    return decoded

#=====================api functions====================================
def route(
    locations_gdf, #a pair of locations in geodataframe fromat
    mode='TRANSIT,WALK',
    trip_name = '',
    date_time = datetime.now(),
    control_vars = dict()): # a dictionary of control variables
    
    #convert the geometry into a list of dictinoaries
    if not locations_gdf.crs:
        print('please define projection for the input gdfs')
        sys.exit()
    
    locations_gdf = locations_gdf.to_crs({'init': 'epsg:4326'})
    
    #convert time into text
    t = date_time.strftime("%H:%M%p")
    d = date_time.strftime("%m-%d-%Y")
    
    #get from and to location from locations_gdf
    orig = locations_gdf['geometry'].iat[0]
    dest = locations_gdf['geometry'].iat[-1]
    
    orig_text = "{0}, {1}".format(orig.y, orig.x)
    dest_text = "{0}, {1}".format(dest.y, dest.x)
    
    #send query to api
    url = 'http://localhost:8080/otp/routers/default/plan'
    query = {
        "fromPlace":orig_text,
        "toPlace":dest_text,
        "time":t,
        "date":d,
        "mode":mode,
        "maxWalkDistance":"1000",
        "arriveBy":"false",
        "wheelchair":"false",
        "locale":"en"}

    r = requests.get(url, params=query)
    
    #if error then return emptly GeoDataFrame
    if 'error' in r.json():
        return gpd.GeoDataFrame()
    
    #convert request output ot a GeoDataFrame
    legs = r.json()['plan']['itineraries'][0]['legs']
    legs_list = list()
    for i, leg in enumerate(legs):
        items = [
            'from',
            'to',
            'distance',
            'duration',
            'startTime',
            'endTime',
            'mode',
            'legGeometry']
        #select only necessary items
        l = {k: leg[k] for k in items}

        #add leg id
        l['leg_id'] = i


        #add leg geometry
        l['geometry'] = geom.LineString(decode(leg['legGeometry']['points']))
        l.pop('legGeometry', None)

        #add origin and destination stops
        if 'stopId' in l['from']:
            l['from_name']=l['from']['stopId']
        else:
            l['from_name'] = l['from']['name']

        if 'stopId' in l['to']:
            l['to_name']=l['to']['stopId']
        else:
            l['to_name'] = l['to']['name']
            
        if 'tripId' in leg:
            l['trip_id']= leg['tripId']
        else:
            l['trip_id'] = ''
            
        if 'routeId' in leg:
            l['route_id']= leg['routeId']
        else:
            l['route_id'] = ''

        #fix from and to to theri locations
        l['from'] = geom.Point(l['from']['lon'], l['from']['lat'])
        l['to'] = geom.Point(l['to']['lon'], l['to']['lat'])


        #convert to dataframe
        l_df = pd.Series(l).to_frame().T

        legs_list.append(l_df)

    legs_df = pd.concat(legs_list).reset_index(drop=True)
    legs_df['trip_name'] = trip_name

    #calculate wait time
    legs_df['waitTime'] = legs_df['startTime'].shift(-1)
    legs_df['waitTime'] = legs_df['waitTime']-legs_df['endTime']
    
    #fix the field order
    field_order = [
        'trip_name',
        'leg_id',
        'mode',
        'from',
        'from_name',
        'to',
        'to_name',
        'route_id',
        'trip_id',
        'distance',
        'duration',
        'startTime',
        'endTime',
        'waitTime',
        'geometry']
    legs_df = legs_df[field_order]
    legs_gdf = gpd.GeoDataFrame(legs_df)
    

    return legs_gdf

def service_area(
    in_gdf, 
    mode = "TRANSIT,WALK", 
    breaks = [500, 1000], #in seconds
    date_time = datetime.now(),
    control_vars = dict()): # a dictionary of control variables
    
    #convert the geometry into a list of dictinoaries
    if not in_gdf.crs:
        print('please define projection for the input gdfs')
        sys.exit()
    
    in_gdf = in_gdf.to_crs({'init': 'epsg:4326'})
    
    #convert time into text
    t = date_time.strftime("%H:%M%p")
    d = date_time.strftime("%Y/%m/%d")
    
    #run for each single point in GeoDataFrame
    url = 'http://localhost:8080/otp/routers/default/isochrone'
    iso_list = list()
    for r in in_gdf.iterrows():
        indx = r[0]
        orig = r[1]['geometry']

        #convert origin from shapely to text
        orig_text = "{0}, {1}".format(orig.y, orig.x)
        
        #send query to api
        query = {
            "fromPlace":orig_text,
            "date":d,
            "time":t,
            "mode":mode,
            "cutoffSec":breaks}

        r = requests.get(url, params=query)
        iso_gdf = gpd.GeoDataFrame.from_features(r.json()['features'])
        iso_list.append(iso_gdf)
    
    out_gdf = pd.concat(iso_list)    
    out_gdf = gpd.GeoDataFrame(out_gdf).copy()
    return out_gdf

def od_matrix_unlimited(
    origins,
    destinations,
    mode,
    origins_name = '',
    destinations_name = '',
    date_time = datetime.now(),
    control_vars = dict()): # a dictionary of control variables
    
    if not origins.crs or not destinations.crs:
        print('please define projection for the input gdfs')
        sys.exit()
    
    #convert the geometry into a list of dictinoaries
    origins = origins.to_crs({'init': 'epsg:4326'})
    destinations = destinations.to_crs({'init': 'epsg:4326'})
    
    od_list = list()
    cnt = 0
    #mark time before start
    t1 = datetime.now()
    print('Analysis started at: {0}'.format(t1))
    
    for o in origins[['geometry', origins_name]].itertuples():
        for d in destinations[['geometry', destinations_name]].itertuples():
            od = pd.DataFrame(
                [[o[1], o[2]],
                 [d[1], d[2]]],
                columns = ['geometry', 'location Name'])
            od = gpd.GeoDataFrame(od, crs = {'init': 'epsg:4326'})
            r = route(
                locations_gdf = od, #a pair of locations in geodataframe fromat
                mode='TRANSIT,WALK',
                trip_name = 'from {0} to {1}'.format(o[2], d[2]),
                date_time = date_time)
            od_list.append(r)
            cnt += 1
            if divmod(cnt, 50)[1] == 0:
                t2 = datetime.now()
                print('Total routes caclulated: {0}, time: {1}'.format(cnt, t2-t1))
                if divmod(cnt, 100000)[1] == 0:
                    temp_save = pd.concat(od_list).reset_index(drop = True)
                    temp_save.to_csv(r'D:\New folder\temp_save.csv')

    od_df = pd.concat(od_list).reset_index(drop = True)
    od_gdf = gpd.GeoDataFrame(od_df, crs = {'init': 'epsg:4326'})
    
    return od_gdf
    
    
    
    
    
    
    
    
    
    
    
    
    