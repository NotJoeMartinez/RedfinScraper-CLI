import re
import json
import datetime as dt
import time

import requests
from bs4 import BeautifulSoup
import numpy as np
import pandas as pd
import pyparsing as pp
import concurrent.futures

import redfin_scraper.config as rsc

import redfin_scraper.resources.logging as rsrl
import redfin_scraper.resources.json_tools as rsrj



class RedfinScraper:

    @rsrl.reset_log
    def __init__(self):

        self.data={}
        self._data_id_ticker=0
        self.df=None
        self.zip_database=pd.DataFrame()





    @rsrl.timing_log
    def setup(self,zip_database_path=rsrj.get_config_value('zip_database_path')):

        if self.zip_database.empty:
            if zip_database_path==None:
                user_agent=self._randomized_UA()

                page_req=requests.get(rsc.ZIP_DATASET_URL.format(rsc.CONST_ZD_URL_EXTENSION),headers=user_agent)
                page_text=page_req.text

                page_soup=BeautifulSoup(page_text,'html.parser')
                csv_link_target=page_soup.find_all('a',{'class':'btn btn-primary'})[2]['href']

                zip_codes_req=requests.get(rsc.ZIP_DATASET_URL.format(csv_link_target),headers=user_agent)
                zip_codes_text=zip_codes_req.text

                zip_list=self._parse_csv(zip_codes_text)

                temp_zip_database=pd.DataFrame(data=zip_list[1:],columns=zip_list[0])
                temp_zip_database_dtype=temp_zip_database.apply(lambda row:pd.to_numeric(row,errors='ignore'))

                self.zip_database=temp_zip_database_dtype

            else:
                try:
                    self.zip_database=pd.read_csv(filepath_or_buffer=zip_database_path)
                except:
                    raise Exception("Could not locate zip_database.csv")





    @rsrl.timing_log
    def get_data(self,id:str=None):
        if id==None:
            output_df=self.df

        else:
            try:
                output_df=self.data[id]

            except KeyError:
                raise Exception("Invalid id. Structure is: D###")

        return output_df





    @rsrl.timing_log
    def scrape(self,city_states:list[str]=rsrj.get_config_value('city_states'),
               zip_codes:list[str]=rsrj.get_config_value('zip_codes'),
               lat_tuner:float=rsrj.get_config_value('lat_tuner'),
               lon_tuner:float=rsrj.get_config_value('lon_tuner')):


        if lat_tuner==None:
            lat_tuner=rsc.DEFAULT_TUNER_VARIABLE
        if lon_tuner==None:
            lon_tuner=rsc.DEFAULT_TUNER_VARIABLE

        lat_tuner=float(lat_tuner)
        lon_tuner=float(lon_tuner)
        

        city_states=self._sanitize_city_states(city_states)

        if ((city_states==None) | (not isinstance(city_states,list))):
            city_states=[]
        else:
            city_states=city_states

        if ((zip_codes==None) | (not isinstance(zip_codes,list))):
            zip_list=[]
        else:
            zip_list=zip_codes


        try:
            if (self.zip_database.empty | self.zip_database==None):
                raise Exception("Did you initialize RedfinScraper.setup?")
        except:
            pass
    
        
        for city_state in city_states:
            zip_list += self._select_zip_codes(city_state,lat_tuner,lon_tuner)

        zip_list=list(set(zip_list)) #Eliminate duplicates


        page_urls=self._generate_urls(zip_codes=zip_list)

        soups=self._threaded_request(self._get_soup,urls=page_urls)

        api_links=self._get_API_links(soups)

        api_urls=self._generate_urls(api_links=api_links)

        api_responses=self._threaded_request(self._get_API_response,urls=api_urls)

        self.df=self._set_dataframe(api_responses)

        self._data_id_ticker+=1
        self.data_id=f"D{self._data_id_ticker:03d}"
        self.data[self.data_id]=self.df

        return self.df









    def _randomized_UA(self):
        num_var=np.random.randint(100,1000)
        num_var3=np.random.randint(10,100)
        num_var2=num_var3%10
        num_var4=np.random.randint(1000,10000)
        num_var5=np.random.randint(100,1000)

        user_agent={"User-Agent": f"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/{num_var5}.36 (KHTML, like Gecko) "+
                f"Chrome/51.{num_var2}.2704.{num_var} Safari/537.{num_var3} OPR/38.0.{num_var4}.41"}
        
        return user_agent





    def _parse_csv(self,csv_file:str) -> list[list]:
            
        csv_contents_list=[]

        split_csv_file=csv_file.splitlines()

        for li in split_csv_file:
            csv_contents_list.append(pp.common.comma_separated_list.parseString(li).asList())

        return csv_contents_list





    def _sanitize_city_states(self,city_states):

        if city_states==None:
            return None

        cleaned_city_states=[]

        for li in city_states:
            if isinstance(li,str):
                cleaned_city_states.append(tuple([ti.strip() for ti in li.split(",")]))
            else:
                tup_li=tuple(li)
                if len(tup_li)>1:
                    cleaned_city_states.append(tup_li)
                else:
                    cleaned_city_states.append(tuple([ti.strip() for ti in tup_li[0].split(",")]))


        return cleaned_city_states





    def _select_zip_codes(self,city_state,lat_tuner,lon_tuner):

        city_df=self.zip_database[(self.zip_database['type']=='STANDARD')
                                    &(self.zip_database['decommissioned']==0)
                                    &((self.zip_database['primary_city'].str.contains(city_state[0].title()))
                                    |(self.zip_database['acceptable_cities'].str.contains(city_state[0].title())))
                                    &(self.zip_database['state']==city_state[1].upper())]
        
        lat_lim,lon_lim=self._lat_lon_limits(city_df,lat_tuner,lon_tuner)

        zip_series=city_df[(city_df['longitude']>lon_lim[0])&
                        (city_df['longitude']<lon_lim[1])&
                        (city_df['latitude']>lat_lim[0])&
                        (city_df['latitude']<lat_lim[1])]['zip']
        
        zip_list=list(map(str,zip_series))
        
        self._check_null(zip_list,city_state)
        
        return zip_list
    
    



    def _lat_lon_limits(self,df,lat_tuner,lon_tuner) -> tuple[tuple,tuple]:
        lon_avg=df['longitude'].mean()
        lat_avg=df['latitude'].mean()

        lon_stdev=df['longitude'].std()
        lat_stdev=df['latitude'].std()

        lon_upper_lim=lon_avg+(lon_tuner*lon_stdev)
        lon_lower_lim=lon_avg-(lon_tuner*lon_stdev)

        lat_upper_lim=lat_avg+(lat_tuner*lat_stdev)
        lat_lower_lim=lat_avg-(lat_tuner*lat_stdev)

        lat_lim=(lat_lower_lim,lat_upper_lim)
        lon_lim=(lon_lower_lim,lon_upper_lim)

        return lat_lim,lon_lim
    




    @rsrl.log_no_zip
    def _check_null(self,zip_list,city_state):
        return zip_list==[]





    def _generate_urls(self,**kwargs):
        urls=[]
        try:
            for zip in kwargs['zip_codes']:
                urls.append(rsc.REDFIN_URL.format(rsc.REDFIN_ZIP_URL.format(zip_code=zip)))
        except:
            pass

        try:
            for link in kwargs['api_links']:
                urls.append(rsc.REDFIN_URL.format(link))
        except:
            pass
            
        return urls
    




    def _threaded_request(self,func,urls):
        responses=[]

        with concurrent.futures.ThreadPoolExecutor() as executor:
            future_to_url = {executor.submit(func,url):url for url in urls}
            for future in concurrent.futures.as_completed(future_to_url):
                url = future_to_url[future]
                try:
                    responses.append(future.result())
                except:
                    pass
                    
        return responses
    




    def _get_API_links(self,soups:list[BeautifulSoup]):
        api_links=[]
        for soup in soups:
            try:
                target=soup.find(rsc.REDFIN_API_CLASS_DEF[0],rsc.REDFIN_API_CLASS_DEF[1])[rsc.REDFIN_API_CLASS_ID]
                api_links.append(target)
            except:
                pass
        return api_links





    def _get_soup(self,url):
        header=self._randomized_UA()

        req=requests.get(url,headers=header)

        if self._check_404(req,url):
            return None

        req_text=req.text

        soup=BeautifulSoup(req_text,'html.parser')

        return soup




    
    @rsrl.log_404
    def _check_404(self,req:requests.Response,url):
        return req.status_code%400 in (1,2,3,4)





    def _get_API_response(self,url):
        header=self._randomized_UA()

        req=requests.get(url,headers=header)

        req_text=req.text

        parsed_response=self._parse_csv(req_text)

        return parsed_response
    
    



    def _set_dataframe(self,api_responses:list[list[list]]):
        df_list=[]

        for response in api_responses:
            df=pd.DataFrame(data=response[1:],columns=response[0])
            df_list.append(df)

        concat_df=pd.concat(df_list,axis=0)
        concat_df=concat_df.apply(lambda row:pd.to_numeric(row,errors='ignore'))
        concat_df.reset_index(inplace=True)

        return concat_df



        




















    

