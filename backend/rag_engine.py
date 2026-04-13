import os
from dotenv import load_dotenv
from langchain_community.document_loaders import PyPDFLoader, TextLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_openai import OpenAIEmbeddings, ChatOpenAI
from langchain_community.vectorstores import FAISS
# 최신 버전에서 권장하는 임포트 방식
from langchain.chains.combine_documents import create_stuff_documents_chain
from langchain.chains.retrieval import create_retrieval_chain
from langchain_core.prompts import ChatPromptTemplate

load_dotenv()  # .env 파일 로드

# Load OpenAI API Key from environment
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

class RAGEngine:
    def __init__(self):
        self.embeddings = OpenAIEmbeddings(openai_api_key=OPENAI_API_KEY)
        self.vector_store = None
        self.llm = ChatOpenAI(model="gpt-4o-mini", temperature=0, openai_api_key=OPENAI_API_KEY)

    def process_document(self, file_path):
        if file_path.endswith(".pdf"):
            loader = PyPDFLoader(file_path)
        else:
            loader = TextLoader(file_path)
        
        documents = loader.load()
        text_splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=200)
        splits = text_splitter.split_documents(documents)
        
        if self.vector_store is None:
            self.vector_store = FAISS.from_documents(splits, self.embeddings)
        else:
            self.vector_store.add_documents(splits)
        
        return "Document processed successfully."

    def ask_chatbot(self, query):
        if self.vector_store is None:
            return "Please upload a document first."
        
        prompt = ChatPromptTemplate.from_template("""
        Answer the following question based only on the provided context:
        <context>
        {context}
        </context>
        Question: {input}
        """)
        
        document_chain = create_stuff_documents_chain(self.llm, prompt)
        retrieval_chain = create_retrieval_chain(self.vector_store.as_retriever(), document_chain)
        
        response = retrieval_chain.invoke({"input": query})
        return response["answer"]

    def generate_questions(self):
        if self.vector_store is None:
            return []
        
        prompt = ChatPromptTemplate.from_template("""
        Based on the following context, generate 5 multiple-choice questions for study. 
        Format the output as a JSON list of objects with 'question', 'options' (list), and 'answer'.
        Do not include markdown markers like ```json.
        Context: {context}
        """)
        
        retriever = self.vector_store.as_retriever(search_kwargs={"k": 5})
        docs = retriever.invoke("Key concepts and main points")
        context = "\n".join([doc.page_content for doc in docs])
        
        response = self.llm.invoke(prompt.format(context=context))
        return response.content
